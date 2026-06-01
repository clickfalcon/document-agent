import json
import httpx  # Using httpx for clean async HTTP requests inside FastAPI
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from mcp import ClientSession

app = FastAPI()

class WorkflowRequest(BaseModel):
    gcs_uri: str
    file_identifier: str
    task_key: str = "tagger"

@app.post("/v1/process-document-pipeline")
async def trigger_pipeline(request: WorkflowRequest, background_tasks: BackgroundTasks):
    """Entry point for the single unified workflow loop."""
    background_tasks.add_task(run_unified_workflow, request)
    return {"status": "queued", "file_id": request.file_identifier}


async def run_unified_workflow(request: WorkflowRequest):
    """Sequential engine coordinating an external REST parsing API and an MCP tool pass."""
    try:
        # ==================================================================
        # STAGE 1: CALL EXTERNAL REST PARSING API
        # ==================================================================
        print(f"[{request.file_identifier}] Launching Stage 1: Offloading to external REST Parser...")
        
        # Define your downstream processing endpoint configuration
        EXTERNAL_PARSER_URL = "https://pdf-parser-service.internal/v1/extract-layout"
        parser_payload = {
            "gcs_uri": request.gcs_uri,
            "target_identifier": request.file_identifier
        }
        
        # Execute the POST call. If the service takes some time, adjust timeout variables.
        async with httpx.AsyncClient(timeout=120.0) as http_client:
            response = await http_client.post(EXTERNAL_PARSER_URL, json=parser_payload)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, 
                    detail=f"Stage 1 parsing failed: {response.text}"
                )
                
            parser_metadata = response.json()
            print(f"[{request.file_identifier}] Stage 1 Success: Files saved directly to GCS by parser service.")

        # ==================================================================
        # STAGE 2: INTELLIGENT TAGGING (MCP & LLM LAYER)
        # ==================================================================
        print(f"[{request.file_identifier}] Starting Stage 2: AI Structural Tagging via MCP...")
        
        # Connect to your shared MCP Server
        async with ClientSession(mcp_tagger_transport) as mcp_session:
            # We simply pass the string identifier token.
            # The MCP server reads the generated components out of GCS locally.
            mcp_response = await mcp_session.call_tool(
                "generate_document_tags", 
                arguments={
                    "file_identifiers": [request.file_identifier], 
                    "task_key": request.task_key
                }
            )
            
            tag_payload = json.loads(mcp_response.content[0].text)

        # ==================================================================
        # STAGE 3: STATE PERSISTENCE (DATABASE LAYER)
        # ==================================================================
        print(f"[{request.file_identifier}] Starting Stage 3: Saving metadata to Database...")
        
        central_db.saved_tags.update_one(
            {"file_id": request.file_identifier},
            {
                "$set": {
                    "tags": tag_payload.get(request.file_identifier),
                    "source_uri": request.gcs_uri,
                    "pipeline_status": "success",
                    "processed_at": "2026-06-01"
                }
            },
            upsert=True
        )
        
        print(f"[Workflow Complete] Unified pipeline succeeded for {request.file_identifier}")

    except Exception as e:
        print(f"[Workflow Failure] Pipeline broken for {request.file_identifier}: {str(e)}")
        central_db.saved_tags.update_one(
            {"file_id": request.file_identifier},
            {"$set": {"pipeline_status": "failed", "error_log": str(e)}},
            upsert=True
        )