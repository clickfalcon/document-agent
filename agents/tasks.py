
from pydantic import BaseModel, Field
from typing import List, Type, Literal, Dict, Optional, Any
from utils.formatting import build_response_schema
from schema import BaseTaskOutput

import json
# =====================================================================

class AgentTask(BaseModel):
    name: str = Field(description="Task name")
    description: Optional[str] = Field(default="", description="Task description")
    task: str = Field(description="Task overview")
    instruction: Optional[str] = Field(description="Step by step instructions")
    response_schema: Optional[Type[BaseModel]] = Field(description="Dynamic Pydantic response schema")
    
    # Few-Shot Examples (Massively improves LLM structural accuracy)
    examples: Optional[List[Dict[str, Any]]] = Field(
        default=None, 
        description="Optional list of input/output examples (few-shot prompting) to guide the LLM."
    )
    
    # Model Parameters (Different tasks require different models or strictness)
    model: str = Field(
        default="gemini-2.0-flash", 
        description="The specific LLM to use for this task (e.g., 'gpt-4o', 'gemini-1.5-pro')."
    )
    temperature: float = Field(
        default=0.0, 
        description="Creativity level. Keep at 0.0 for strict structural data extraction like building plans."
    )
    
    # Max Retries (Crucial for handling dynamic schema parsing failures)
    max_retries: int = Field(
        default=3, 
        description="Number of times the agent should retry fixing its output if validation fails."
    )


# =====================================================================
# 1. Define Output Schemas for the Automated Multimodal Report
# =====================================================================

# Simulate pulling the updated JSON from your DB
compliance_json_raw = """
{
  "is_compliant": {
    "type": "boolean",
    "description": "Set to True if zero critical issues or code conflicts are found.",
    "required": true
  },
  "issues_found": {
    "type": "array",
    "description": "A detailed catalog of flagged code issues.",
    "required": false,
    "items": {
      "severity": {
        "type": ["Critical Violation", "Warning", "Needs Clarification"],
        "description": "The risk level of the issue.",
        "required": true
      },
      "category": {
        "type": "string",
        "description": "The compliance category, e.g., 'ADA Accessibility'.",
        "required": true
      },
      "location": {
        "type": "string",
        "description": "Where the issue is located on the plan.",
        "required": true
      },
      "description": {
        "type": "string",
        "description": "Detailed explanation of what fails.",
        "required": true
      },
      "suggested_action": {
        "type": "string",
        "description": "Recommended fix or RFI instructions.",
        "required": false
      }
    }
  }
}
"""

compliance_checking_task = AgentTask(
    name = "building_code_compliance_checker",
    description = "Reviews building plans against local building codes, fire safety, and accessibility standards.",
    task = "Flag potential code violations or discrepancies found within the architectural drawings.",
    response_schema = build_response_schema(json.loads(compliance_json_raw)),
    instruction = (
        "Analyze the layout for regulatory compliance. Verify that door swing clearances, corridor widths, "
        "travel distances to exits, and restroom layouts meet standard accessibility (e.g., ADA) and "
        "life safety codes. Highlight any dimensions or configurations that appear to violate these standards "
        "or require manual RFI (Request for Information) clarification."
    )
)

class VisualDiscrepancyItem(BaseModel):
    element_id: str = Field(description="The identifier or label of the item (e.g., 'Station 11 Nodes', 'Sacred Heart Status')")
    markdown_text_claim: str = Field(description="What the textual layer or table claims")
    visual_map_evidence: str = Field(description="What the actual physical image diagram shows visually")
    discrepancy_analysis: str = Field(description="Explanation of the contradiction between the image and text blocks")
    severity: str = Field(description="Severity rating: CRITICAL, WARNING, or TYPO")

class TagsItem(BaseModel):
    tag_name: str
    tag_value: str

class TaggingTaskOutput(BaseTaskOutput):
    tags: List[TagsItem]

class MultimodalAuditReport(BaseTaskOutput):
    document_name: str
    overall_data_integrity_score: int = Field(description="Score from 1 to 10 indicating data consistency")
    visual_contradictions: List[VisualDiscrepancyItem] = Field(description="List of all discrepancies caught by inspecting the pictures")
    remediation_steps: str = Field(description="Recommended data-engineering steps to fix the pipeline source mapping mismatch")

summary_task = AgentTask(
    name = "summary_notes_summarizer",
    description = "Condenses raw project notes, meeting transcripts, or field updates into structured, actionable summaries.",
    task = "Generate a concise summary, key decisions, and action items from the provided document.",
    response_schema = BaseTaskOutput,
    instruction = (
        "Review the input document and extract the core discussion points only from the Summary Notes. "
        "Your output must include: 1) A high-level executive summary (2-3 sentences), "
        "2) A bulleted list of key decisions made, and 3) A distinct list of action items, "
        "including who is responsible and deadlines if mentioned. Avoid fluff and maintain "
        "a professional, objective tone."
    )
)

tagging_task = AgentTask(
    name = "tagger",
    description = "Extracts relevant construction and project management metadata from the document.",
    task = "Identify and extract all construction and project-related tags from the document.",
    response_schema = TaggingTaskOutput,
    instruction = (
        "Analyze the provided document and extract key terms, categories, or concepts "
        "related to construction (e.g., materials, methods, safety, equipment) and "
        "project management (e.g., phases, milestones, roles, scheduling). Also include entiries like names, addresses, dates, places and currencies. Return these "
        "tags in a clean, standardized format matching the response schema."
    )
)
sample1 = AgentTask(
    name = "discrepancy analysis task",
    description="",
    task =  """
        Your task is to cross-examine text claims against physical visual elements inside the attached diagram files. 
        Specifically, look for instances where text charts conflict with visual map layouts (e.g., station sequences or tracking indexes changing names between the text layers and the visual diagram drawings).   
        """,
    instruction = "",
    response_schema = MultimodalAuditReport
)

sample2 = AgentTask(
    name = "Linear Chainage & Station Sequencing Alignment",
    description="Cross-checks kilometer (KM) points and tracking indexes between track spreadsheets and geometric alignment maps to catch numbering or location drifts.",
    task= """
        Audit linear chainage coordinates (e.g., "KM 12+350") and station tracking sequences across text schedules and physical alignment map drawings.
        """,
    instruction = """

    1. Trace the sequence of stations and switch turnouts on the physical layout map from start to finish.

    2. Match their mapped physical locations against the station names, coordinates, and exact kilometer indexes listed in the technical track alignment text tables.

    3. Scan for skipped indexes, inversions in tracking sequence order, or names changing between the text data layer and the visual drawing drawings.

    4. Classify sequence inversions or shifted station markers as CRITICAL due to downstream signal mapping errors.

    """,
    response_schema = BaseTaskOutput
)

task1 = AgentTask(
    name = "Civil Clearance & Dynamic Envelope Verification",
    description="Identifies structural violations where physical concrete/tunnel dimensions in drawings mismatch dimensions listed in the dynamic train vehicle clearance sheets.",
    task = """
        Cross-examine structural clearance tables and vehicle dynamic envelope dimensions against civil cross-section drawings and tunnel profile schematics.
        """,
    instruction = """
        1. Locate dimensional callouts on structural elements (e.g., tunnel linings, walkway widths, platform edge coping) within the provided CAD or PDF drawings.

        2. Compare these physical drawing dimensions against the minimum clearance parameters specified in the textual design criteria tables.

        3. Flags instances where structural elements visually infringe upon the required train vehicle buffer envelope.

        4. Classify structural infringements that would physically damage a moving train as CRITICAL.
        """,
    response_schema = BaseTaskOutput
)

task3 = AgentTask(
    name = "Traction Power Supply & Substation Mapping",
    description="Matches electrical loads, substation ratings, and equipment tags between cable/transformer schedules and single-line diagrams (SLDs).",
    task = """
        Cross-examine electrical load lists and procurement schedules against Traction Power Substation (TPSS) Single Line Diagrams (SLDs) and Overhead Contact System (OCS) drawings.
        """,
    instruction = """
        1. Extract alphanumeric equipment tags (e.g., "TPSS-02-TR-01") and power ratings (kVA/MW) from the textual bill of materials or schedules.

        2. Locate those identical equipment nodes on the visual Single Line Diagram drawing.

        3. Verify that the power capacity numbers, voltage limits (e.g., 750V DC vs 25kV AC), and physical feeding zone boundaries match perfectly between both sources.

        4. Mark mismatched transformer capacities or mislabeled feeding zones as WARNING or CRITICAL depending on downstream system impacts.

        5. Provide recommendations to solve the mismatches
        """,
    response_schema = BaseTaskOutput
)

task4 = AgentTask(
    name = "Signaling Block & Communication Asset Mapping",
    description="Verifies hardware counts, telemetry sensors, and signaling blocks between bills of quantities and wayside schematic layouts.",
    task = """
        Audit wayside signaling block markers, transponders (balises), CCTV, and public address assets by cross-referencing asset tracking tables against network topology drawings.
        """,
    instruction = """
        1. Count the physical quantity and sequence of signaling block markers or wayside sensors shown on the track layout drawing.

        2. Compare this visual count and asset tag naming structure against the telemetry schedule and procurement bill of quantities (BoQ) data layer.

        3. Inspect for missing hardware nodes on the drawing that are claimed in text lists, or mismatched sensor labels that will break automated train control (ATC) software mappings.

        4. Mark any discrepancy in signaling block identifiers, missing sensors, or telemetry tag mismatches as CRITICAL.
        """,
    response_schema = BaseTaskOutput
)

task5 = AgentTask(
    name = "Life Safety, Fire, & Egress Spatial Compliance",
    description="Audits emergency escape route widths, fire-rated doors, and safety assets against architectural blueprints and NFPA compliance tables.",
    task = """
        Validate station emergency egress calculations against fire evacuation zone drawings and structural architectural layouts.
        """,
    instruction = """
        1. Read the text-based egress capacity tables stating the minimum required clear widths for emergency stairs, corridors, and turnstiles.

        2. Use the architectural drawing scales or dimensions to verify that the physical spaces provided on the blueprint match or exceed those minimum width claims.

        3. Cross-reference the fire-rated barrier classifications (e.g., 2-hour fire wall) noted in structural descriptions with the partition boundaries visually highlighted on the safety layout drawings.

        4. Classify undersized emergency exits, unmapped exit paths, or missing fire doors as CRITICAL.
        """,
    response_schema = BaseTaskOutput
)

