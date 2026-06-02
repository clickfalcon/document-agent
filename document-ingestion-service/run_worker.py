from worker import DocumentIngestionWorker
import sys
import os

worker = DocumentIngestionWorker(destination_uri='./artifacts/')

filename = sys.argv[1]

# todo: add organization_id	
worker.process_document(source_uri=filename, document_id=os.path.basename(filename).replace('.',''))