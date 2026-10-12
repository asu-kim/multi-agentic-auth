from typing import Literal
from pydantic import Field
from agents.common.search_models import StrictModel


class TaskRequest(StrictModel):
    protocol: Literal["agent-task/v1"] = "agent-task/v1"
    action: Literal["execute"] = "execute"
    query: str = Field(min_length=1, max_length=32000)


class TaskAnswer(StrictModel):
    status: Literal["completed", "input_required", "declined"]
    output: str = Field(min_length=1)


class TaskResult(StrictModel):
    protocol: Literal["agent-task/v1"] = "agent-task/v1"
    agent_name: str
    status: Literal["completed", "simulated", "input_required", "declined", "error"]
    output: str
