from ingestion_worker import CloudDocumentIngestionWorker
import sys
import os

worker = CloudDocumentIngestionWorker(output_base_dir='processed_artifacts')
print(worker)

filename = sys.argv[1]
print(filename)

worker.process_document(filename, os.path.basename(filename))