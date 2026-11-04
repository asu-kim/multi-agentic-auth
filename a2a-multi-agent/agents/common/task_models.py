from typing import Literal
from pydantic import Field
from agents.common.search_models import ResourceGroup, StrictModel


class ResourceEndpoint(StrictModel):
    host: str = Field(min_length=1, pattern=r"^\S+$")
    port: int = Field(ge=1, le=65535)


class TaskRequest(StrictModel):
    protocol: Literal["agent-task/v1"] = "agent-task/v1"
    action: Literal["execute"] = "execute"
    query: str = Field(min_length=1, max_length=32000)
    delegated_groups: list[ResourceGroup] | None = None
    resource_endpoints: dict[ResourceGroup, ResourceEndpoint] | None = None
    requested_tool: Literal["deliver_to_safe_zone"] | None = None


class TaskAnswer(StrictModel):
    requires_safe_zone_delivery: bool = Field(default=False, description="True for retrieving an item and delivering it through a gate into a safe or secure zone.")
    status: Literal["completed", "input_required", "declined"]
    output: str = Field(min_length=1)


class TaskResult(StrictModel):
    protocol: Literal["agent-task/v1"] = "agent-task/v1"
    agent_name: str
    status: Literal["completed", "simulated", "input_required", "declined", "error"]
    output: str
