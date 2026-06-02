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
    def __init__(self):
        self.converter = self._build_docling_converter()
    
    def is_gcs(self, uri):
        return uri.startswith("gs://")

    def _save_artifact(self, artifacts_uri: str, relative_path: str, content, content_type="application/octet-stream"):
        if self.is_gcs(artifacts_uri):
            parts = artifacts_uri.replace("gs://", "").split("/", 1)
            
            bucket_name = parts[0]
            base_blob_path = parts[1] if len(parts) > 1 else ""
            blob_name = f"{base_blob_path.rstrip('/')}/{relative_path.lstrip('/')}".strip("/")
            blob = self.gcs_client.bucket(bucket_name).blob(blob_name)

            if isinstance(content, io.BytesIO):
                blob.upload_from_file(content, content_type=content_type)
            else:
                blob.upload_from_string(content, content_type=content_type)
        else:
            base_path = Path(artifacts_uri)
            full_path = (base_path / relative_path).resolve()
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


    def _compile_lean_spatial_layout(self, doc, raw_layout_map: dict) -> dict:
        """
        Processes the heavy layout properties using Docling's native SDK objects
        to guarantee perfect table row_index and col_index structural mapping.
        """
        content_elements = []
        visual_blocks = []
        pages_config = {}

        # 1. Parse Text Fragments safely
        for block in raw_layout_map.get("texts", []):
            text_content = block.get("text", "").strip()
            if not text_content:
                continue
            prov = block.get("prov", [])
            page_no = 1
            bbox_coords = None
            if prov and isinstance(prov, list):
                page_no = prov[0].get("page_no", 1)
                b = prov[0].get("bbox", {})
                bbox_coords = [b.get("l"), b.get("t"), b.get("r"), b.get("b")]
            content_elements.append({
                "page_no": page_no,
                "label": block.get("label", "text"),
                "text": text_content,
                "bbox": bbox_coords
            })

        # 2. Extract Embedded Drawing / Picture Regions
        for pic in raw_layout_map.get("pictures", []):
            prov = pic.get("prov", [])
            page_no = 1
            bbox_coords = None
            if prov and isinstance(prov, list):
                page_no = prov[0].get("page_no", 1)
                b = prov[0].get("bbox", {})
                bbox_coords = [b.get("l"), b.get("t"), b.get("r"), b.get("b")]
            visual_blocks.append({
                "page_no": page_no,
                "type": "picture",
                "label": pic.get("label", "picture"),
                "uri": pic.get("image", {}).get("uri") if pic.get("image") else None,
                "bbox": bbox_coords
            })

        # 3. Extract Tables Natively Using Docling SDK Objects
        if hasattr(doc, "tables") and doc.tables:
            for table_element in doc.tables:
                page_no = 1
                table_bbox = None
                
                # Extract parent table coordinates from its top-level layout provenance
                if hasattr(table_element, "prov") and table_element.prov:
                    page_no = table_element.prov[0].page_no
                    b = table_element.prov[0].bbox
                    table_bbox = [b.l, b.t, b.r, b.b]

                visual_blocks.append({
                    "page_no": page_no,
                    "type": "table",
                    "label": "table",
                    "bbox": table_bbox
                })

                # Loop through the SDK cells grid matrix safely
                for cell in table_element.data.table_cells:
                    cell_text = cell.text.strip() if cell.text else ""
                    if not cell_text:
                        continue

                    # Safe attribute extraction for row/col matrix indices
                    row_index = cell.row_index if hasattr(cell, "row_index") else 0
                    col_index = cell.col_index if hasattr(cell, "col_index") else 0

                    # Fix: TableCell objects hold spatial coordinates in cell.bbox
                    cell_bbox = None
                    if hasattr(cell, "bbox") and cell.bbox:
                        cb = cell.bbox
                        cell_bbox = [cb.l, cb.t, cb.r, cb.b]
                    
                    # Fallback to parent table bounding box if cell dimensions are omitted
                    if not cell_bbox:
                        cell_bbox = table_bbox

                    content_elements.append({
                        "page_no": page_no,
                        "label": "table_cell",
                        "row_index": row_index,
                        "col_index": col_index,
                        "text": cell_text,
                        "bbox": cell_bbox
                    })

        # 4. Enumerate Page Configuration Baselines
        pages = raw_layout_map.get("pages", {})
        for p_key, p_val in pages.items():
            size = p_val.get("size", {})
            pages_config[str(p_key)] = {
                "width": size.get("width"),
                "height": size.get("height"),
                "unit": "points"
            }

        return {
            "document_meta": {
                "name": raw_layout_map.get("name", "document"),
                "version": raw_layout_map.get("version", "1.0.0"),
                "origin_filename": raw_layout_map.get("origin", {}).get("filename", "")
            },
            "pages_config": pages_config,
            "visual_blocks": visual_blocks,
            "content_elements": content_elements
        }

    def process_document(self, source_uri: str, artifacts_uri: str):
            """
            Executes document parsing, exports binary images from processing memory,
            and runs a schema structural check pass to format layout URIs natively.
            Accommodates both Local paths and GCS paths natively.
            """

            if self.is_gcs(source_uri) or self.is_gcs(artifacts_uri):
                self.gcs_client = storage.Client()

            input_path = Path(self._fetch_input_file(source_uri))
            
            # Log paths carefully
            logger.debug(f"\nExecution Run: {source_uri=} {artifacts_uri=}")
            logger.debug(f'{source_uri=} {artifacts_uri=}')       
            try:
                # Initialize pipeline converter pass
                start_time = time.perf_counter()
                result = self.converter.convert(input_path)
                doc = result.document
                
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

                            img_buffer = io.BytesIO()
                            if hasattr(element.image, "pil_image"):
                                element.image.pil_image.save(img_buffer, format="PNG")
                            else:
                                element.image.save(img_buffer, format="PNG")
                            
                            img_buffer.seek(0)

                            relative_path_slug = f"extracted_images/{img_filename}"
                            self._save_artifact(
                                artifacts_uri=artifacts_uri, 
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

                self._save_artifact(
                    artifacts_uri=artifacts_uri, 
                    relative_path="clean_document.md",
                    content="\n".join(enhanced_markdown_lines),
                    content_type="text/markdown"
                )

                # ------------------------------------------------------------------
                # PASS 3 & 4: Process Layout Map purely in-memory
                # ------------------------------------------------------------------
                logger.debug("-> Preparing layout schema...")
                
                if hasattr(doc, "model_dump"):
                    raw_layout_map = doc.model_dump()
                elif hasattr(doc, "export_to_dict"):
                    raw_layout_map = doc.export_to_dict()
                else:
                    # Fallback if only json serialization is exposed
                    raw_layout_map = json.loads(doc.model_dump_json())

                logger.debug("-> Executing Structural Post-Processing Pass...")
                json_image_counter = 0

                def clean_by_schema_structure(node, current_page=1):
                    nonlocal json_image_counter
                    
                    if isinstance(node, dict):
                        if "page_no" in node:
                            try:
                                current_page = int(node["page_no"])
                            except:
                                pass
                        
                        if "uri" in node and isinstance(node["uri"], str) and (node["uri"].startswith("data:") or len(node["uri"]) > 150):
                            node["uri"] = f"extracted_images/page_1_1.png"

                        is_image_profile = "mimetype" in node and "size" in node and ("uri" in node or "bytes" in node)
                        
                        if is_image_profile:
                            json_image_counter += 1
                            # FIX 3: Fixed minor structural bug where you printed literal '.page_no' extension string
                            img_filename = f"page_{current_page}_{json_image_counter}.png"
                            
                            node["mimetype"] = "image/png"
                            node["uri"] = f"extracted_images/{img_filename}"
                            
                            for heavy_key in ["bytes", "data", "image_base64", "value"]:
                                if heavy_key in node:
                                    del node[heavy_key]
                                    
                            logger.debug(f"   [Structural Clean] Formatted profile block for: {img_filename}")
                        else:
                            for k in list(node.keys()):
                                clean_by_schema_structure(node[k], current_page)
                    elif isinstance(node, list):
                        for item in node:
                            clean_by_schema_structure(item, current_page)

                # Fire the targeted structural analyzer pass in memory
                clean_by_schema_structure(raw_layout_map)

                optimized_layout = self._compile_lean_spatial_layout(doc, raw_layout_map)
                
                # Save the processed layout map
                self._save_artifact(
                    artifacts_uri=artifacts_uri, 
                    relative_path="full_layout.json", 
                    content=json.dumps(optimized_layout, indent=2), 
                    content_type="application/json"
                )
                logger.debug("[Post-Process Success] Schema profiles realigned. Base64 layers purged.")

                # ------------------------------------------------------------------
                # PASS 5: Render Standard Tracking Manifest
                # ------------------------------------------------------------------
                metadata_payload = {
                    "source_uri": source_uri,
                    "artifacts_uri": artifacts_uri,
                    "total_extracted_images_count": max(image_counter, json_image_counter)
                }

                self._save_artifact(
                    artifacts_uri=artifacts_uri, 
                    relative_path="metadata.json", 
                    content=json.dumps(metadata_payload, indent=2), 
                    content_type="application/json"
                )
                    
                logger.debug(f"\n[Success] Run artifacts cleanly generated via target URI adapter.")

                # Cleanup the downloaded temp file if it was created from GCS
                if source_uri.startswith("gs://") and input_path.exists():
                    os.remove(input_path)

                end_time = time.perf_counter()
                logger.debug(f"Elapsed time: {round(end_time - start_time,2)} seconds")
            except Exception as e:
                logger.error(f"[Execution Failure] Processing pipeline faulted: {str(e)}")
                if source_uri.startswith("gs://") and input_path.exists():
                    os.remove(input_path)
                raise e
