from typing import Literal
from pydantic import Field, PrivateAttr
from agents.common.search_models import ResourceGroup, StrictModel


class ResourceEndpoint(StrictModel):
    host: str = Field(min_length=1, pattern=r"^\S+$")
    port: int = Field(ge=1, le=65535)


class DispatchTimestamp(StrictModel):
    monotonic_ns: int = Field(ge=0)
    unix_ns: int = Field(ge=0)
    clock_domain: str = Field(min_length=1, max_length=256)


class TaskRequest(StrictModel):
    protocol: Literal["agent-task/v1"] = "agent-task/v1"
    action: Literal["execute"] = "execute"
    query: str = Field(min_length=1, max_length=32000)
    delegated_groups: list[ResourceGroup] | None = None
    resource_endpoints: dict[ResourceGroup, ResourceEndpoint] | None = None
    requested_tool: Literal["deliver_to_safe_zone"] | None = None
    trace_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    dispatch_timing: DispatchTimestamp | None = None
    # Receiver timestamps are local observations, never supplied by the sender.
    _received_timing: dict | None = PrivateAttr(default=None)


class TaskAnswer(StrictModel):
    requires_safe_zone_delivery: bool = Field(default=False, description="True for retrieving an item and delivering it through a gate into a safe or secure zone.")
    status: Literal["completed", "input_required", "declined"]
    output: str = Field(min_length=1)


class TaskResult(StrictModel):
    latency: dict | None = None
    protocol: Literal["agent-task/v1"] = "agent-task/v1"
    agent_name: str
    status: Literal["completed", "simulated", "input_required", "declined", "error"]
    output: str
