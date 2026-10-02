from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=8000)
    # An empty list means the manager LLM interprets the natural-language query.
    required_capabilities: list[str] = Field(default_factory=list)


class Assessment(StrictModel):
    can_help: bool
    reason: str


class NegotiationRequest(StrictModel):
    protocol: Literal["capability-negotiation/v1"] = "capability-negotiation/v1"
    action: Literal["propose", "confirm"]
    request_id: str
    query: str = Field(min_length=1, max_length=8000)
    required_capabilities: list[str] = Field(min_length=1)
    offer_id: str | None = None


class NegotiationReply(StrictModel):
    protocol: Literal["capability-negotiation/v1"] = "capability-negotiation/v1"
    request_id: str
    status: Literal["offered", "confirmed", "declined", "error"]
    agent_name: str
    capabilities: list[str]
    reason: str
    offer_id: str | None = None


class SelectedAgent(StrictModel):
    name: str
    url: str
    capabilities: list[str]
    offer_id: str


class Attempt(StrictModel):
    agent_name: str
    agent_url: str
    phase: Literal["discovery", "proposal", "confirmation"]
    status: str
    detail: str


class SearchResult(StrictModel):
    status: Literal["found", "not_found", "input_required", "error"]
    query: str
    required_capabilities: list[str]
    selected_agent: SelectedAgent | None = None
    summary: str
    discovered_agents: list[dict] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)
    # Executed tool actions and observations, not the model's private reasoning.
    manager_actions: list[dict] = Field(default_factory=list)

    # One record per completed model response; text is copied from provider fields.
    manager_turns: list[dict] = Field(default_factory=list)
