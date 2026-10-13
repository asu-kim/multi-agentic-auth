"""Manager decisions backed by discovered cards; delegate authority and dispatch to the selected worker."""
from typing import Literal
from uuid import uuid4
from agents.common.task_models import TaskRequest, TaskResult

from agents.manager_agent.iotauth_delegate import IoTAuthDelegate

from a2a.types import AgentCard
from pydantic import Field

from agents.common.search_models import Attempt, SearchRequest, SearchResult, SelectedAgent, StrictModel


def normalize_capabilities(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip().lower().replace("-", "_").replace(" ", "_")
                             for value in values if value.strip()))


class AgentAddress(StrictModel):
    agent_url: str = Field(description="An address from the configured agent roster.")


class Requirements(StrictModel):
    capabilities: list[str] = Field(min_length=1, description="ALL required skill IDs, including unsupported abilities.")


class CardAssessment(AgentAddress):
    suitable: bool = Field(description="Whether the entire original task fits the card's skills AND limits.")
    reason: str = Field(min_length=1, max_length=4000, description="Explain your decision using the query and card constraints.")


class Finish(StrictModel):
    status: Literal["found", "not_found"]
    agent_url: str | None = Field(description="Positively assessed agent URL for found; null for not_found.")


class Clarification(StrictModel):
    question: str = Field(min_length=1, max_length=2000)


TOOL_SPECS = {
    "discover_agent": (AgentAddress, "Fetch an A2A card to learn skills and limits. A refresh invalidates this agent's assessment. Can retry errors."),
    "set_requirements": (Requirements, "Set ALL required capabilities; preserve unsupported abilities. Explicit user IDs cannot change. Changes clear assessments."),
    "assess_agent": (CardAssessment, "Record YOUR suitability judgment based on the discovered card and original task, including all limits. No request is sent to the agent."),
    "delegate_authority": (AgentAddress, "After a positive assessment, grant the identified agent's configured IoTAuth group authority over Resources for 1*day through Auth. Call before finish_search. A failed or uncertain request must not be retried automatically."),
    "dispatch_task": (AgentAddress, "After successful delegation, send the selected agent its task over A2A. For a secure-communication capable robot, request connection to its configured target; otherwise forward the original task. Observe the worker result before finishing. Never retry uncertain execution."),
    "finish_search": (Finish, "Select a positively assessed card matching all required skills, or report not_found after inspecting the roster and rejecting every skill-matching candidate."),
    "ask_user": (Clarification, "Ask a specific question if requirements or feasibility details are missing."),
}
TOOLS = [
    {"type": "function", "function": {"name": name, "description": description,
                                      "parameters": schema.model_json_schema()}}
    for name, (schema, description) in TOOL_SPECS.items()
]


class ManagerTools:
    """Per-search evidence; the LLM chooses actions and assesses free-text limits."""

    def __init__(self, request: SearchRequest, urls: tuple[str, ...], peer):
        self.request, self.peer = request, peer
        self.urls = dict.fromkeys(url.rstrip("/") for url in urls)
        self.cards: dict[str, AgentCard] = {}
        self.inspected: set[str] = set()
        self.discovery_errors: set[str] = set()
        self.required = normalize_capabilities(request.required_capabilities)
        self.explicit_requirements = bool(request.required_capabilities)
        self.assessments: dict[str, CardAssessment] = {}
        self.attempts: list[Attempt] = []
        self.actions: list[dict] = []
        self.finished: SearchResult | None = None
        self.delegations: dict[str, dict] = {}
        self.dispatched_url: str | None = None
        self.task_result: dict | None = None

    def address(self, value: str) -> str:
        url = value.rstrip("/")
        if url not in self.urls:
            raise ValueError("Use an agent_url from the configured roster.")
        return url

    async def execute(self, name: str, arguments: dict) -> dict:
        if name not in TOOL_SPECS:
            raise ValueError(f"Unknown tool: {name}")
        schema, _ = TOOL_SPECS[name]
        params = schema.model_validate(arguments)
        return await getattr(self, name)(params)

    async def discover_agent(self, args: AgentAddress) -> dict:
        url = self.address(args.agent_url)
        self.inspected.add(url)
        self.assessments.pop(url, None)
        try:
            card = await self.peer.discover(url + "/")
            if card.url.rstrip("/") != url:
                raise ValueError("The card advertises a different endpoint from the configured address.")
            self.cards[url] = card
            self.discovery_errors.discard(url)
            return {"agent_card": card.model_dump(mode="json", by_alias=True, exclude_none=True)}
        except Exception as exc:
            self.cards.pop(url, None)
            self.discovery_errors.add(url)
            self.attempts.append(Attempt(agent_name="Unknown", agent_url=url + "/", phase="discovery",
                                         status="error", detail=str(exc)))
            return {"error": str(exc), "agent_url": url, "retry_possible": True}

    async def set_requirements(self, args: Requirements) -> dict:
        required = normalize_capabilities(args.capabilities)
        if not required:
            raise ValueError("Specify at least one nonempty capability or ask the user for clarification.")
        if self.explicit_requirements and set(required) != set(self.required):
            raise ValueError("Cannot change explicit requirements.")
        if set(required) != set(self.required):
            self.assessments.clear()
        self.required = required
        return {"required_capabilities": self.required}

    def missing_skills(self, card: AgentCard) -> set[str]:
        return set(self.required) - {skill.id for skill in card.skills}

    async def assess_agent(self, args: CardAssessment) -> dict:
        url = self.address(args.agent_url)
        if url not in self.cards:
            raise ValueError("Discover this agent before assessing it.")
        if not self.required:
            raise ValueError("Set required capabilities before assessing an agent.")
        if not args.reason.strip():
            raise ValueError("Provide a nonempty card-based assessment reason.")
        missing = self.missing_skills(self.cards[url])
        if args.suitable and missing:
            raise ValueError(f"Card lacks required capabilities: {', '.join(sorted(missing))}.")
        assessment = args.model_copy(update={"agent_url": self.cards[url].url})
        self.assessments[url] = assessment
        self.attempts.append(Attempt(agent_name=self.cards[url].name, agent_url=self.cards[url].url,
            phase="assessment", status="suitable" if args.suitable else "unsuitable", detail=args.reason))
        return assessment.model_dump()

    async def delegate_authority(self, args: AgentAddress) -> dict:
        url = self.address(args.agent_url)
        assessment = self.assessments.get(url)
        if (url not in self.cards or assessment is None or not assessment.suitable
                or not self.required or self.missing_skills(self.cards[url])):
            raise ValueError("Discover and positively assess the agent before delegating authority.")
        if url not in self.delegations:
            self.delegations[url] = await IoTAuthDelegate().delegate(url)
        return self.delegations[url]

    async def dispatch_task(self, args: AgentAddress) -> dict:
        url = self.address(args.agent_url)
        assessment = self.assessments.get(url)
        if (url not in self.cards or assessment is None or not assessment.suitable
                or not self.required or self.missing_skills(self.cards[url])):
            raise ValueError("Discover and positively assess the selected agent before dispatch.")
        if self.delegations.get(url, {}).get("status") != "delegated":
            raise ValueError("Successful authority delegation is required before dispatch.")
        if self.dispatched_url is not None:
            if self.dispatched_url != url:
                raise ValueError("A task was already dispatched to another agent in this search.")
            return self.task_result
        card = self.cards[url]
        query = self.request.query
        if "secure_communication" in {skill.id for skill in card.skills}:
            query = "Establish secure communication with your configured target resource using establish_secure_communication. Report whether the connection succeeded."
        # Record before sending: failed/uncertain requests must not be replayed.
        self.dispatched_url = url
        self.task_result = {"status": "error", "output": "Worker request interrupted; execution outcome unknown. Do not retry automatically."}
        try:
            response = await self.peer.send(card, TaskRequest(query=query).model_dump(), uuid4().hex)
            self.task_result = TaskResult.model_validate(response).model_dump()
        except Exception as exc:
            self.task_result = {"status": "error", "output": f"Worker request failed; execution may be uncertain: {exc}"}
        return self.task_result

    async def finish_search(self, args: Finish) -> dict:
        if not self.required:
            raise ValueError("Set required capabilities or ask the user for clarification before finishing.")
        if args.status == "found":
            url = self.address(args.agent_url or "")
            assessment = self.assessments.get(url)
            if url not in self.cards or assessment is None or not assessment.suitable:
                raise ValueError("Cannot report found without a positive card-based assessment.")
            delegation = self.delegations.get(url)
            if delegation is not None and delegation.get("status") != "delegated":
                self.finished = self.result("error", "Agent identified, but Auth delegation was not confirmed; see manager_actions. Do not retry automatically: the grant may have been applied.")
                return {"status": self.finished.status, "summary": self.finished.summary}
            if self.dispatched_url is not None and self.dispatched_url != url:
                raise ValueError("Finish with the agent that received the dispatched task.")
            if delegation and self.dispatched_url is None:
                raise ValueError("Dispatch the task to the delegated agent before finishing.")
            card = self.cards[url]
            if self.missing_skills(card):
                raise ValueError("Selected card does not cover all required capabilities.")
            selected = SelectedAgent(name=card.name, url=card.url,
                capabilities=[s.id for s in card.skills], reason=assessment.reason)
            summary = f"Selected {selected.name}: {assessment.reason}"
            if delegation:
                summary += f" Auth confirmed delegation to {delegation['subject']}."
            status = "found"
            if self.task_result is not None:
                worker_status = self.task_result["status"]
                summary += f" Worker reported {worker_status}: {self.task_result['output']}"
                if worker_status == "input_required":
                    status = "input_required"
                elif worker_status in {"error", "declined"}:
                    status = "error"
            else:
                summary += " Availability and execution have not been checked."
            self.finished = self.result(status, summary, selected)
        else:
            if args.agent_url is not None:
                raise ValueError("Use null for agent_url when reporting not_found.")
            unchecked = set(self.urls) - self.inspected
            if unchecked:
                raise ValueError(f"Inspect the remaining agents before reporting not_found: {sorted(unchecked)}")
            unresolved = [url for url, card in self.cards.items() if not self.missing_skills(card)
                          and (url not in self.assessments or self.assessments[url].suitable)]
            if unresolved:
                raise ValueError(f"Assess matching agents as unsuitable or select a suitable one: {unresolved}")
            if self.discovery_errors:
                self.finished = self.result("error", "The search is incomplete because some cards could not be retrieved; see attempts.")
            else:
                self.finished = self.result("not_found", "No suitable agent was found based on the configured agents' cards.")
        return {"status": self.finished.status, "summary": self.finished.summary}

    async def ask_user(self, args: Clarification) -> dict:
        if not args.question.strip():
            raise ValueError("Provide a nonempty clarification question.")
        self.finished = self.result("input_required", args.question)
        return {"status": "input_required", "question": args.question}

    def result(self, status: str, summary: str, selected: SelectedAgent | None = None) -> SearchResult:
        return SearchResult(status=status, query=self.request.query, required_capabilities=self.required,
            selected_agent=selected, summary=summary, attempts=list(self.attempts),
            task_result=self.task_result,
            assessments=[a.model_dump() for a in self.assessments.values()],
            discovered_agents=[{"name": card.name, "url": card.url, "capabilities": [s.id for s in card.skills],
                                "description": card.description} for card in self.cards.values()],
            manager_actions=list(self.actions))
