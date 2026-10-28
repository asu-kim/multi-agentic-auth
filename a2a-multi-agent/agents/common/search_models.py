from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DelegationScope(StrictModel):
    resource: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    validity: str = Field(max_length=32, pattern=r"^[1-9][0-9]{0,8}\*(sec|min|hour|day)$")


class DelegationPreferences(StrictModel):
    resource: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    validity: str | None = Field(default=None, max_length=32, pattern=r"^[1-9][0-9]{0,8}\*(sec|min|hour|day)$")


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=8000)
    # An empty list means the manager LLM interprets the natural-language query.
    required_capabilities: list[str] = Field(default_factory=list)
    delegation: DelegationScope | DelegationPreferences | None = None


class SelectedAgent(StrictModel):
    name: str
    url: str
    capabilities: list[str]
    reason: str


class Attempt(StrictModel):
    agent_name: str
    agent_url: str
    phase: Literal["discovery", "assessment"]
    status: str
    detail: str


class SearchResult(StrictModel):
    status: Literal["found", "not_found", "input_required", "error"]
    query: str
    required_capabilities: list[str]
    selected_agent: SelectedAgent | None = None
    task_result: dict | None = None
    revocation_result: dict | None = None
    resolved_delegation: DelegationScope | None = None
    resource_selection_reason: str | None = None
    summary: str
    discovered_agents: list[dict] = Field(default_factory=list)
    attempts: list[Attempt] = Field(default_factory=list)
    assessments: list[dict] = Field(default_factory=list)
    # Executed tool actions and observations, not the model's private reasoning.
    manager_actions: list[dict] = Field(default_factory=list)

    # One record per completed model response; text is copied from provider fields.
    manager_turns: list[dict] = Field(default_factory=list)
