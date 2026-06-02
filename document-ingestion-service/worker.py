import os
import io
import re
import json
import uuid
from pathlib import Path
from loguru import logger
from google.cloud import storage
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
import time

class DocumentIngestionWorker:
    def __init__(self, destination_uri: str = "/tmp/processed_artifacts"):
        self.destination_uri = destination_uri
        self.is_gcs = destination_uri.startswith("gs://")
        self.converter = self._build_docling_converter()
        
        if self.is_gcs:
            self.gcs_client = storage.Client()
            parts = self.destination_uri.replace("gs://", "").split("/", 1)
            self.bucket_name = parts[0]
            self.base_blob_path = parts[1] if len(parts) > 1 else ""
        else:
            self.base_path = Path(destination_uri)
            self.base_path.mkdir(parents=True, exist_ok=True)

        logger.debug(f'{destination_uri=}')

    def _sanitize_id(self, document_id: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_-]', '', document_id)

    def _save_artifact(self, document_id: str, relative_path: str, content, content_type="application/octet-stream"):
        safe_document_id = self._sanitize_id(document_id)

        if self.is_gcs:
            target_key = f"{self.base_blob_path}/{safe_document_id}/{relative_path}".strip("/")
            logger.debug(f'{target_key=}')
            
            blob = self.gcs_client.bucket(self.bucket_name).blob(target_key)
            if isinstance(content, io.BytesIO):
                blob.upload_from_file(content, content_type=content_type)
            else:
                blob.upload_from_string(content, content_type=content_type)
        else:
            full_path = (self.base_path / safe_document_id / relative_path).resolve()
            full_path.parent.mkdir(parents=True, exist_ok=True)
            logger.debug(f'{full_path=}')
            if isinstance(content, io.BytesIO):
                with open(full_path, "wb") as f: f.write(content.getvalue())
            else:
                with open(full_path, "w" if isinstance(content, str) else "wb") as f: f.write(content)

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
        
        logger.debug("[OCR Upgrade] Registering Tesseract Engine for server-side execution...")
        pipeline_options.ocr_options = TesseractOcrOptions() 

        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.IMAGE: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

    def _fetch_input_file(self, source_uri: str) -> str:
            """
            If the path is a GCS URI (gs://...), download it to a local 
            temporary file and return the local path. Otherwise, return the original path.
            """
            if source_uri.startswith("gs://"):
                # Ensure the directory exists
                Path("/tmp").mkdir(parents=True, exist_ok=True)
                local_tmp = f"/tmp/{uuid.uuid4().hex}_{os.path.basename(source_uri)}"
                
                # Extract bucket and blob names
                parts = source_uri.replace("gs://", "").split("/", 1)
                bucket_name = parts[0]
                blob_name = parts[1]
                
                # Download
                logger.info(f"Downloading from GCS: {source_uri} to {local_tmp}")
                bucket = self.gcs_client.bucket(bucket_name)
                blob = bucket.blob(blob_name)
                blob.download_to_filename(local_tmp)
                
                return local_tmp
                
            return source_uri

    def process_document(self, source_uri: str, document_id: str):
        """
        Executes document parsing, exports binary images from processing memory,
        and runs a schema structural check pass to format layout URIs natively.
        """

        input_path = Path(self._fetch_input_file(source_uri))

        destination_folder = Path(os.path.join(self.destination_uri, document_id))

        logger.debug(f"\n[Execution Run: {document_id}] Loading target file: {input_path.name}")
        
        logger.debug(f'{source_uri=}')
        logger.debug(f'{document_id=}')
        logger.debug(f'{destination_folder=}')
               
        try:
            # Initialize pipeline converter pass
            start_time = time.perf_counter()
            result = self.converter.convert(input_path)
            doc = result.document
            
            # Establish workspace directories for physical file storage
            images_base_dir = destination_folder / "extracted_images"
            images_base_dir.mkdir(parents=True, exist_ok=True)
            
            # ------------------------------------------------------------------
            # PASS 1: Extract and Save Physical Images From Direct Canvas Memory
            # ------------------------------------------------------------------
            logger.debug("-> Extracting and saving physical document images from canvas memory...")
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

                        # Create an in-memory buffer
                        img_buffer = io.BytesIO()

                        if hasattr(element.image, "pil_image"):
                            element.image.pil_image.save(img_buffer, format="PNG")
                        else:
                            element.image.save(img_buffer, format="PNG")
                        
                        # Seek back to the start so the uploader can read it
                        img_buffer.seek(0)

                        relative_path_slug = f"extracted_images/{img_filename}"
                        self._save_artifact(
                            document_id=document_id, 
                            relative_path=relative_path_slug, 
                            content=img_buffer, 
                            content_type="image/png"
                        )
                        
                        ordered_image_paths.append({
                            "path": relative_path_slug,
                            "page_no": page_no,
                            "label": label_str
                        })

            # ------------------------------------------------------------------
            # PASS 2: Compile Custom Markdown featuring structural tags
            # ------------------------------------------------------------------
            logger.debug("-> Compiling enhanced context markdown metadata file...")
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
            logger.debug("-> Serializing full_layout.json data...")
            
            json_out = destination_folder / "full_layout.json"

            # Get the JSON data as a string
            if hasattr(doc, "model_dump_json"):
                json_content = doc.model_dump_json(indent=2)
            else:
                export_data = doc.model_dump() if hasattr(doc, "model_dump") else doc.export_to_dict()
                json_content = json.dumps(export_data, indent=2, default=str)

            # Use your abstraction to save it
            self._save_artifact(
                document_id=document_id, 
                relative_path="full_layout.json", 
                content=json_content, 
                content_type="application/json"
            )

            # ------------------------------------------------------------------
            # PASS 4: RECURSIVE SCHEMA-STRUCTURE POST-PROCESS PURGE
            # ------------------------------------------------------------------
            logger.debug("-> Executing Structural Post-Processing Pass...")
            
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
                        node["uri"] = f"extracted_images/page_1_1.png"

                    # CRITICAL MATCH: Check if this node matches the structure of a Docling image canvas block
                    is_image_profile = "mimetype" in node and "size" in node and ("uri" in node or "bytes" in node)
                    
                    if is_image_profile:
                        json_image_counter += 1
                        img_filename = f"page_{current_page}_{json_image_counter}.page_no"
                        
                        # 1. Enforce the exact structure layout schema
                        node["mimetype"] = "image/png"
                        node["uri"] = f"extracted_images/{img_filename}"
                        
                        # 2. Purely delete any lingering memory buffer keys that hold the text pixel rows
                        for heavy_key in ["bytes", "data", "image_base64", "value"]:
                            if heavy_key in node:
                                del node[heavy_key]
                                
                        logger.debug(f"   [Structural Clean] Formatted profile block for: {img_filename}")
                    
                    else:
                        # Continue walking down the dictionary paths
                        for k in list(node.keys()):
                            clean_by_schema_structure(node[k], current_page)
                            
                elif isinstance(node, list):
                    for item in node:
                        clean_by_schema_structure(item, current_page)

            # Fire the targeted structural analyzer pass
            clean_by_schema_structure(raw_layout_map)

            self._save_artifact(
                document_id=document_id, 
                relative_path="full_layout.json", 
                content=json.dumps(raw_layout_map, indent=2), 
                content_type="application/json"
            )
            
            logger.debug("[Post-Process Success] Schema profiles realigned. Base64 layers purged.")

            # ------------------------------------------------------------------
            # PASS 5: Render Standard Tracking Manifest
            # ------------------------------------------------------------------
            meta_out = destination_folder / "metadata.json"
            metadata_payload = {
                "doc_id": document_id,
                "original_filename": input_path.name,
                "total_extracted_images_count": max(image_counter, json_image_counter)
            }

            self._save_artifact(
                document_id=document_id, 
                relative_path="metadata.json", 
                content=json.dumps(metadata_payload, indent=2), 
                content_type="application/json"
            )
                
            logger.debug(f"\n[Success] Run artifacts cleanly generated inside: {destination_folder}")

            end_time = time.perf_counter()
            logger.debug(f"Elapsed time: {round(end_time - start_time,2)} seconds")
        except Exception as e:
            logger.debug(f"[Execution Failure] Processing pipeline faulted: {str(e)}")
            raise e

