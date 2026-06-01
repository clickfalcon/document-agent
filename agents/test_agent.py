import sys
sys.path.extend('.')
from agents import agent
from tasks import *
import json

if __name__ == "__main__":

    # Test execution harness loop
    # Ensure you export GEMINI_API_KEY prior to execution
    agent = agent.Agent(artifact_base_dir="processed_artifacts")
    
    try:
        final_report = agent.run_task(task=summary_task, files="plan.pdf")
        print(json.dumps(final_report, indent=2))

    except Exception as e:
        print(f"[Failure] Multimodal check faulted: {str(e)}")