import os
import re
import json
from pathlib import Path

from google import genai
from google.genai import types

from tasks import *
from typing import List, Union

# =====================================================================
# 2. Multimodal Agent Worker Implementation
# =====================================================================
class Agent:
    def __init__(self, artifact_base_dir: str = "processed_artifacts"):
        self.artifact_base_dir = Path(artifact_base_dir)
        self.client = genai.Client()
        self.system_instruction = """
        You are an advanced, high-precision Technical Document Analyst. 

        Your core operational rules are:
        1. Always evaluate provided text data arrays, tables, and visual file artifacts objectively.
        2. Rely strictly on the provided context; do not extrapolate or assume missing metrics.
        3. If outputting JSON, strictly validate your response against the requested schema layout.
        4. Maintain a formal, analytical engineering tone at all times.
        """

    def _automatically_parse_and_load_images(self, markdown_text: str, run_folder: Path) -> List[types.Part]:
        """
        Natively scans the markdown string for <image path="..."> tags,
        reads the file binaries from the disk path, and formats them for the Gemini SDK.
        """
        gemini_image_parts = []
        
        # Regex to locate the exact path parameters injected by our custom Phase 1 pipeline
        image_tag_regex = r'<image\s+path=["\']([^"\']+)["\']'
        found_paths = re.findall(image_tag_regex, markdown_text)
        
        print(f"-> Automated Parser found {len(found_paths)} unique image anchors in the markdown context.")
        
        for relative_path_str in found_paths:
            # Reconstruct the absolute path mapping inside the container storage
            absolute_disk_path = run_folder / relative_path_str
            
            if absolute_disk_path.exists():
                print(f"   Loading physical binary asset: {relative_path_str}")
                with open(absolute_disk_path, "rb") as img_file:
                    img_bytes = img_file.read()
                
                # Wrap raw file bytes into the official GenAI API structural object
                part = types.Part.from_bytes(
                    data=img_bytes,
                    mime_type="image/png"
                )
                gemini_image_parts.append(part)
            else:
                print(f"   [Warning] Markdown referenced an asset that is missing on disk: {absolute_disk_path}")
                
        return gemini_image_parts

    def run_task(self, task: AgentTask, files: Union[list[str], str]) -> dict:
            """Aggregates multiple files, layout layers, and visual sheets into a single

            multimodal context pass, generating a unified cross-document audit report.
            """

            if isinstance(files, str):
                files = [files]

            if not files:
                raise ValueError("The files / files batch array cannot be empty.")

            contents_payload = [
                f"""
                --- GLOBAL TASK CONTEXT ---
                {task.description}

                {task.instruction}

                You are auditing a collection of {len(files)} interconnected documents or layout sheets simultaneously. 
                Analyze all provided text maps, dimensional parameters, and accompanying visual layouts collectively.
                Your final JSON response must map your structural evaluations cleanly across all processed sources.
                """
            ]

            print(f"-> Aggregating {len(files)} document matrices into a unified payload layer...")

            # 1. Harvest and chain all document matrices into the single prompt array
            for file_identifier in files:
                target_folder = self.artifact_base_dir / file_identifier
                markdown_path = target_folder / "clean_document.md"
                json_path = target_folder / "full_layout.json"

                if not markdown_path.exists() or not json_path.exists():
                    print(f"[Warning] Skipping integration for {file_identifier}: Missing tracking artifacts.")
                    continue

                with open(markdown_path, "r", encoding="utf-8") as f:
                    markdown_content = f.read()

                with open(json_path, "r", encoding="utf-8") as f:
                    layout_content = f.read()

                # Append the text context segment for this specific file block
                contents_payload.append(
                    f"""
                    ======================================================================
                    START OF DOCUMENT REPOSITORY: {file_identifier}
                    ======================================================================
                    
                    --- DOCUMENT TEXT CONTENT LAYER ({file_identifier}) ---
                    {markdown_content}
                    
                    --- SCHEMA STRUCTURE LAYOUT LAYER ({file_identifier}) ---
                    {layout_content}
                    
                    --- VISUAL ASSETS / IMAGES FOR {file_identifier} ---
                    The images following this label belong exclusively to the document '{file_identifier}'.
                    """
                )

                # 2. Extract this specific document's images
                visual_assets = self._automatically_parse_and_load_images(markdown_content, target_folder)
                
                # Interleave them immediately after the text block for this specific file
                for idx, asset in enumerate(visual_assets):
                    contents_payload.append(f"[Image {idx + 1} for Document: {file_identifier}]")
                    contents_payload.append(asset) # This is your raw image data block

                contents_payload.append(
                    f"""
                    ======================================================================
                    END OF DOCUMENT REPOSITORY: {file_identifier}
                    ======================================================================
                    """
                )


            print("-> Initializing cross-document aggregate multimodal model execution via gemini-2.5-flash...")

            # 3. Fire a single request encompassing the entire structural ecosystem
            response = self.client.models.generate_content(
                model=task.model,
                contents=contents_payload,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    system_instruction=self.system_instruction,
                    response_schema=task.response_schema,  # Crucial: Ensure this schema expects grouped/mapped data
                    temperature=task.temperature
                )
            )

            # 4. Process and store the unified master evaluation report
            report_data = json.loads(response.text)
            report_data['aggregated_batch_task'] = task.name
            report_data['processed_sources'] = files

            # Save standard reference copy in your base execution workspace
            master_output_path = self.artifact_base_dir / "aggregated_multimodal_audit_report.json"
            with open(master_output_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2)

            print(f"[Success] Aggregated execution finished. Consolidated data layout saved at: {master_output_path}")
            return report_data