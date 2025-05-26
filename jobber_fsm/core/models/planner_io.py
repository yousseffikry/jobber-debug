from pydantic import BaseModel

class PlannerInput(BaseModel):
    query: str        # free-form directive from orchestrator

class PlannerOutput(BaseModel):
    terminate: bool   # True when planning is done
    content: str      # plan (or final answer) in plain text or JSON
