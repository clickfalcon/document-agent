from worker import DocumentIngestionWorker
import sys


worker = DocumentIngestionWorker()

worker.process_document(source_uri=filename, artifacts_uri="./artifacts/abcd123/efg456/")