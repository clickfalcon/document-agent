import os
import json
from pathlib import Path

# Import clean stable base components
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

class CloudDocumentIngestionWorker:
    def __init__(self, output_base_dir: str = "/tmp/processed_artifacts"):
        self.output_base_dir = Path(output_base_dir)
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

    def _build_docling_converter(self) -> DocumentConverter:
        """Configures Docling to use Tesseract OCR optimized for Linux Server instances."""
        os.environ["OMP_LOG_LEVEL"] = "DISABLED"
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.generate_page_images = True
        pipeline_options.generate_picture_images = True
        pipeline_options.images_scale = 2.0 
        pipeline_options.accelerator_options.device = "cpu"
        
        print("[OCR Upgrade] Registering Tesseract Engine for server-side execution...")
        pipeline_options.ocr_options = TesseractOcrOptions() 

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

    def process_document(self, input_file_path: str, run_id: str):
        """Executes parsing, saves binary images natively, and outputs enhanced markdown metadata."""
        input_path = Path(input_file_path)
        destination_folder = self.output_base_dir / run_id
        destination_folder.mkdir(parents=True, exist_ok=True)
        
        print(f"\n[Execution Run: {run_id}] Loading target file: {input_path.name}")
        
        try:
            converter = self._build_docling_converter()
            result = converter.convert(input_path)
            doc = result.document
            
            # --- Establish Storage Directories for Physical Binary Exports ---
            images_base_dir = destination_folder / "extracted_images"
            standard_view_dir = images_base_dir
            standard_view_dir.mkdir(parents=True, exist_ok=True)
            
            # 1. First Pass: Scan elements via doc.iterate_items() to extract and save PIL handles natively
            print("-> Extracting and saving physical document images from canvas...")
            image_counter = 0
            ordered_image_paths = []

            for element, level in doc.iterate_items():
                label_str = str(getattr(element, "label", "")).lower()
                
                # Check if the element is structurally flagged as a visual component
                if "picture" in label_str or "figure" in label_str or "graphic" in label_str:
                    # Safely check if the element has an image payload attribute
                    if hasattr(element, "image") and element.image:
                        image_counter += 1
                        
                        # Extract the page context safe reference
                        page_no = 1
                        if hasattr(element, "prov") and element.prov:
                            page_no = getattr(element.prov[0], "page_no", 1)
                            
                        img_filename = f"page_{page_no}_{image_counter}.png"
                        physical_disk_path = standard_view_dir / img_filename
                        
                        # Access the native underlying PIL image model to save to disk
                        # Modern Docling wraps the raw PIL handle inside element.image.pil_image
                        if hasattr(element.image, "pil_image"):
                            element.image.pil_image.save(physical_disk_path)
                        else:
                            element.image.save(physical_disk_path)
                            
                        relative_path_link = f"extracted_images/standard_view/{img_filename}"
                        print(f"   Saved Image Asset to Disk: {relative_path_link}")
                        
                        # Store properties sequentially to reconstruct the custom markdown file tags
                        ordered_image_paths.append({
                            "path": relative_path_link,
                            "page_no": page_no,
                            "label": label_str
                        })

            # 2. Second Pass: Structural Markdown Construction with Custom Image Tags
            print("-> Compiling enhanced context markdown file...")
            enhanced_markdown_lines = []
            md_image_idx = 0
            
            for element, level in doc.iterate_items():
                label_str = str(getattr(element, "label", "")).lower()
                
                if "picture" in label_str or "figure" in label_str or "graphic" in label_str:
                    if md_image_idx < len(ordered_image_paths):
                        meta = ordered_image_paths[md_image_idx]
                        img_tag = f'\n<image path="{meta["path"]}" type="{meta["label"]}" page={meta["page_no"]}>\n'
                        md_image_idx += 1
                    else:
                        img_tag = f'\n<image path="extracted_images/standard_view/unknown_{md_image_idx+1}.png" type="picture" page=1>\n'
                        md_image_idx += 1
                        
                    enhanced_markdown_lines.append(img_tag)
                else:
                    if hasattr(element, "export_to_markdown"):
                        enhanced_markdown_lines.append(element.export_to_markdown())
                    elif hasattr(element, "text") and element.text:
                        enhanced_markdown_lines.append(f"\n{element.text}\n")

            # Save down the enhanced MD context file
            markdown_out = destination_folder / "clean_document.md"
            with open(markdown_out, "w", encoding="utf-8") as f:
                f.write("\n".join(enhanced_markdown_lines))

            # 3. Third Pass: Export structural full_layout JSON map safely
            print("-> Serializing full_layout.json map structure...")
            json_out = destination_folder / "full_layout.json"
            with open(json_out, "w", encoding="utf-8") as f:
                if hasattr(doc, "model_dump_json"):
                    # Use Pydantic's C-based engine to cleanly convert the objects into a clean JSON layout string
                    json_string = doc.model_dump_json(indent=2)
                    f.write(json_string)
                else:
                    export_data = doc.model_dump() if hasattr(doc, "model_dump") else doc.export_to_dict()
                    json.dump(export_data, f, indent=2, default=str)

            # 4. Render standard tracking manifest layout file
            meta_out = destination_folder / "metadata.json"
            metadata_payload = {
                "doc_id": run_id,
                "original_filename": input_path.name,
                "total_extracted_images_count": image_counter
            }
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(metadata_payload, f, indent=2)
                
            print(f"[Success] Run artifacts cleanly generated inside: {destination_folder}")
            print(f"Total physical images saved to disk: {image_counter}")
            
        except Exception as e:
            print(f"[Execution Failure] Processing pipeline faulted: {str(e)}")
            raise e