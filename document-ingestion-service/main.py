import os
import uuid
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.background import BackgroundTasks
from loguru import logger
from worker import DocumentIngestionWorker

# 1. Configuration: Use env var or default
DESTINATION_URI = os.getenv("DESTINATION_URI", "/tmp/artifacts")
worker = DocumentIngestionWorker(destination_uri=DESTINATION_URI)

# 2. Background Task Handler
def run_background_process(temp_path: str, run_id: str):
    """Executes the Docling worker and handles file cleanup."""
    try:
        worker.process_document(temp_path, run_id)
        logger.success(f"Background run {run_id} completed successfully.")
    except Exception as e:
        logger.exception(f"Background processing failed for {run_id}: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
            logger.debug(f"Temporary file cleaned up: {temp_path}")

# 3. Request Handler
async def process_document(request):
    """Starlette route for document ingestion."""
    form = await request.form()
    file = form.get("file")
    run_id = form.get("run_id", str(uuid.uuid4()))

    if not file:
        return JSONResponse({"error": "No file provided"}, status_code=400)

    # Save incoming upload to temporary storage
    temp_path = f"/tmp/{run_id}_{file.filename}"
    content = await file.read()
    with open(temp_path, "wb") as buffer:
        buffer.write(content)
    
    # Trigger background work
    tasks = BackgroundTasks()
    tasks.add_task(run_background_process, temp_path, run_id)
    
    logger.info(f"Task {run_id} received and queued for background processing.")
    
    return JSONResponse(
        {"status": "accepted", "run_id": run_id, "message": "Processing started in background"},
        background=tasks
    )

# 4. Route Definition
routes = [
    Route("/process_document", endpoint=process_document, methods=["POST"]),
]

app = Starlette(debug=False, routes=routes)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)