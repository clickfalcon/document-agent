import os
import json
from pathlib import Path

# Import clean, stable core components from Docling
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

class CloudDocumentIngestionWorker:
    def __init__(self, output_base_dir: str = "/tmp/processed_artifacts"):
        """
        Initializes the ingestion worker.
        NOTE: Google Cloud Run filesystems are read-only except for /tmp 
        (which allocates temporary space straight out of instance RAM).
        """
        self.output_base_dir = Path(output_base_dir)
        self.output_base_dir.mkdir(parents=True, exist_ok=True)

    def _build_docling_converter(self) -> DocumentConverter:
        """Configures Docling to use Tesseract OCR optimized for serverless environments."""
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
        """
        Executes document parsing, exports binary images from processing memory,
        and runs a schema structural check pass to format layout URIs natively.
        """
        input_path = Path(input_file_path)
        destination_folder = self.output_base_dir / run_id
        destination_folder.mkdir(parents=True, exist_ok=True)
        
        print(f"\n[Execution Run: {run_id}] Loading target file: {input_path.name}")
        
        try:
            # Initialize pipeline converter pass
            converter = self._build_docling_converter()
            result = converter.convert(input_path)
            doc = result.document
            
            # Establish workspace directories for physical file storage
            images_base_dir = destination_folder / "extracted_images"
            images_base_dir.mkdir(parents=True, exist_ok=True)
            
            # ------------------------------------------------------------------
            # PASS 1: Extract and Save Physical Images From Direct Canvas Memory
            # ------------------------------------------------------------------
            print("-> Extracting and saving physical document images from canvas memory...")
            image_counter = 0
            ordered_image_paths = []

            for element, level in doc.iterate_items():
                label_str = str(getattr(element, "label", "")).lower()
                
                if "picture" in label_str or "figure" in label_str or "graphic" in label_str:
                    if hasattr(element, "image") and element.image:
                        image_counter += 1
                        
                        page_no = 1
                        if hasattr(element, "prov") and element.prov:
                            page_no = getattr(element.prov[0], "page_no", 1)
                            
                        img_filename = f"page_{page_no}_{image_counter}.png"
                        
                        if hasattr(element.image, "pil_image"):
                            element.image.pil_image.save(images_base_dir / img_filename)
                        else:
                            element.image.save(images_base_dir)
                            
                        relative_path_slug = f"extracted_images/{img_filename}"
                        
                        ordered_image_paths.append({
                            "path": relative_path_slug,
                            "page_no": page_no,
                            "label": label_str
                        })

            # ------------------------------------------------------------------
            # PASS 2: Compile Custom Markdown featuring structural tags
            # ------------------------------------------------------------------
            print("-> Compiling enhanced context markdown metadata file...")
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
                        img_tag = f'\n<image path="extracted_images/unknown_{md_image_idx+1}.png" type="picture" page=1>\n'
                        md_image_idx += 1
                        
                    enhanced_markdown_lines.append(img_tag)
                else:
                    if hasattr(element, "export_to_markdown"):
                        enhanced_markdown_lines.append(element.export_to_markdown())
                    elif hasattr(element, "text") and element.text:
                        enhanced_markdown_lines.append(f"\n{element.text}\n")

            markdown_out = destination_folder / "clean_document.md"
            with open(markdown_out, "w", encoding="utf-8") as f:
                f.write("\n".join(enhanced_markdown_lines))

            # ------------------------------------------------------------------
            # PASS 3: Generate Standard Master JSON Output
            # ------------------------------------------------------------------
            print("-> Writing preliminary full_layout.json mapping data...")
            json_out = destination_folder / "full_layout.json"
            with open(json_out, "w", encoding="utf-8") as f:
                if hasattr(doc, "model_dump_json"):
                    f.write(doc.model_dump_json(indent=2))
                else:
                    export_data = doc.model_dump() if hasattr(doc, "model_dump") else doc.export_to_dict()
                    json.dump(export_data, f, indent=2, default=str)

            # ------------------------------------------------------------------
            # PASS 4: RECURSIVE SCHEMA-STRUCTURE POST-PROCESS PURGE
            # ------------------------------------------------------------------
            print("-> Executing Structural Post-Processing Pass...")
            
            with open(json_out, "r", encoding="utf-8") as f:
                raw_layout_map = json.load(f)

            json_image_counter = 0

            def clean_by_schema_structure(node, current_page=1):
                """
                Traverses the JSON tree. When it finds a dictionary containing 
                Docling's signature image keys, it standardizes the schema structural profile.
                """
                nonlocal json_image_counter
                
                if isinstance(node, dict):
                    # Capture page numbers as we pass through top-level page lists
                    if "page_no" in node:
                        try:
                            current_page = int(node["page_no"])
                        except:
                            pass
                    
                    # Look for top-level file references (like origin blocks)
                    if "uri" in node and isinstance(node["uri"], str) and (node["uri"].startswith("data:") or len(node["uri"]) > 150):
                        node["uri"] = f"extracted_images/page_1_standard_1.png"

                    # CRITICAL MATCH: Check if this node matches the structure of a Docling image canvas block
                    is_image_profile = "mimetype" in node and "size" in node and ("uri" in node or "bytes" in node)
                    
                    if is_image_profile:
                        json_image_counter += 1
                        img_filename = f"page_{current_page}_standard_{json_image_counter}.page_no"
                        
                        # 1. Enforce the exact structure layout schema
                        node["mimetype"] = "image/png"
                        node["uri"] = f"extracted_images/{img_filename}"
                        
                        # 2. Purely delete any lingering memory buffer keys that hold the text pixel rows
                        for heavy_key in ["bytes", "data", "image_base64", "value"]:
                            if heavy_key in node:
                                del node[heavy_key]
                                
                        print(f"   [Structural Clean] Formatted profile block for: {img_filename}")
                    
                    else:
                        # Continue walking down the dictionary paths
                        for k in list(node.keys()):
                            clean_by_schema_structure(node[k], current_page)
                            
                elif isinstance(node, list):
                    for item in node:
                        clean_by_schema_structure(item, current_page)

            # Fire the targeted structural analyzer pass
            clean_by_schema_structure(raw_layout_map)

            # Rewrite the finalized, completely lightweight data structure back to disk
            with open(json_out, "w", encoding="utf-8") as f:
                json.dump(raw_layout_map, f, indent=2)
            
            print("   [Post-Process Success] Schema profiles realigned. Base64 layers purged.")

            # ------------------------------------------------------------------
            # PASS 5: Render Standard Tracking Manifest
            # ------------------------------------------------------------------
            meta_out = destination_folder / "metadata.json"
            metadata_payload = {
                "doc_id": run_id,
                "original_filename": input_path.name,
                "total_extracted_images_count": max(image_counter, json_image_counter)
            }
            with open(meta_out, "w", encoding="utf-8") as f:
                json.dump(metadata_payload, f, indent=2)
                
            print(f"\n[Success] Run artifacts cleanly generated inside: {destination_folder}")
            
        except Exception as e:
            print(f"[Execution Failure] Processing pipeline faulted: {str(e)}")
            raise e
