import os
import re
import json
from pathlib import Path

from google import genai
from google.genai import types

from tasks import *
from typing import List

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

    def run_task(self, task:AgentTask, file: str) -> dict:
        """Automatically aggregates text metadata maps and image assets to audit file logic."""
        target_folder = self.artifact_base_dir / file
        markdown_path = target_folder / "clean_document.md"
        json_path = target_folder / "full_layout.json"

        if not markdown_path.exists() or not json_path.exists():
            raise FileNotFoundError(f"Missing required Phase 1 output matrices inside {target_folder}")

        # 1. Read document layers into workspace strings
        with open(markdown_path, "r", encoding="utf-8") as f:
            markdown_content = f.read()

        with open(json_path, "r", encoding="utf-8") as f:
            layout_content = f.read()

        # 2. AUTOMATIC IMAGE HARVESTING PASS
        # Automatically extracts, reads, and converts the saved .png files from disk memory
        visual_assets = self._automatically_parse_and_load_images(markdown_content, target_folder)

        # 4. Compile the Prompt Payload Array
        # Standard texts and layout dictionary schema strings
        contents_payload = [
            f"""

            --- TASK ---
            {task.description}

            {task.instruction}

            --- DOCUMENT TEXT CONTENT LAYER ---
            {markdown_content}
            
            --- SCHEMA STRUCTURE LAYOUT LAYER ---
            {layout_content}
            """
        ]
        
        # Dynamically append the gathered physical image binary blocks natively into the contents frame
        contents_payload.extend(visual_assets)

        print(contents_payload)

        print("-> Initializing model execution over combined multimodal matrices...")
        
        print(task)
        
        # Request strict Pydantic JSON schema generation from gemini-2.5-flash
        response = self.client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents_payload,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                system_instruction=self.system_instruction,
                response_schema=task.response_schema,
                temperature=task.temperature# Zero variance for absolute analytical accuracy
            )
        )

        # Parse and dump audit data back onto local filesystem
        report_data = json.loads(response.text)
        report_data['task'] = task.name
        output_report_path = target_folder / "multimodal_audit_report.json"
        with open(output_report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)

        print(f"[Success] Multimodal Agent pass finished. Report compiled in: {output_report_path}")
        return report_data

