
from pydantic import BaseModel, Field

class BaseTaskOutput(BaseModel):
    task_output:str = Field(description="Task output")
