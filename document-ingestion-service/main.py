import os
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from loguru import logger
from worker import DocumentIngestionWorker
from pydantic import BaseModel, Field, ValidationError
import sys
from dotenv import load_dotenv

# Define structural schema validation profile
class DocumentParameters(BaseModel):
    source_uri: str = Field(description='Input document uri')
    artifacts_uri: str = Field(description='Artifacts uri')

# 2. Health Check Handler
async def health_check(request):
    """Liveness and Readiness probe endpoint."""
    return JSONResponse({"status": "healthy"}, status_code=200)

# 3. Synchronous Wait Request Handler
async def process_document(request):
    """
    Starlette route for synchronous document ingestion.
    Validates payload against the DocumentParameters schema.
    """
    try:
        raw_body = await request.json()
        # FIX 1: Initializing model with keyword arguments
        body = DocumentParameters(**raw_body)
    except ValidationError as val_err:
        logger.warning(f"Payload validation failed: {val_err.errors()}")
        return JSONResponse({"success": False, "error": "Validation Error", "details": val_err.errors()}, status_code=422)
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid or missing JSON payload"}, status_code=400)

    # Local extractions from the validated schema object
    source_uri = body.source_uri
    artifacts_uri = body.artifacts_uri

    logger.info(f"Received processing request: {source_uri=} {artifacts_uri=}")

    try:
        # The API caller blocks here until the Docling pipeline     letely runs
        worker = DocumentIngestionWorker()
        worker.process_document(
            source_uri=source_uri,
            artifacts_uri=artifacts_uri
        )
             
        # FIX 2: Dynamically resolve target folder mapping uri cleanly
  
        logger.success(f"Processing for document_id {source_uri} completed successfully.")
        
        return JSONResponse({
            "success": True,
            "source_uri": source_uri,
            "artifacts_uri": artifacts_uri
        }, status_code=200)

    except Exception as e:
        logger.exception(f"Processing pipeline faulted for source_uri {source_uri}: {str(e)}")
        return JSONResponse({
            "success": False,
            "source_uri": source_uri,
            "error": str(e)
        }, status_code=500)

# 4. Route Definitions
routes = [
    Route("/health", endpoint=health_check, methods=["GET"]),
    Route("/process_document", endpoint=process_document, methods=["POST"]),
]

app = Starlette(debug=False, routes=routes)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)