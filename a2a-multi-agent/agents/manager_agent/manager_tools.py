"""Actions available to the manager LLM, backed by validated A2A exchanges."""
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from a2a.types import AgentCard
from pydantic import Field

from agents.common.negotiation_models import (
    Attempt, NegotiationReply, NegotiationRequest, SearchRequest, SearchResult,
    SelectedAgent, StrictModel,
)


def normalize_capabilities(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip().lower().replace("-", "_").replace(" ", "_")
                             for value in values if value.strip()))


class ToolArguments(StrictModel):
    decision_summary: str = Field(default="", max_length=240, description=(
        "One short user-facing sentence describing the purpose of this action, based on the request "
        "or an observed tool result. Do not include internal deliberation or claim an unobserved outcome."
    ))


class AgentAddress(ToolArguments):
    agent_url: str = Field(description="An address from the configured agent roster.")


class Requirements(ToolArguments):
    capabilities: list[str] = Field(min_length=1, description="ALL required skill IDs, including unsupported abilities.")


class Finish(ToolArguments):
    status: Literal["found", "not_found"]
    agent_url: str | None = Field(description="Confirmed agent URL for found; null for not_found.")


class Clarification(ToolArguments):
    question: str = Field(min_length=1, max_length=2000)


TOOL_SPECS = {
    "discover_agent": (AgentAddress, "Fetch an agent's A2A card to learn its name, skills and limits. Can retry after an error."),
    "set_requirements": (Requirements, "Set all capabilities required by the query. Use card skill IDs for synonyms; preserve unknown abilities. Explicit user capability IDs cannot change. Requirements lock after the first proposal."),
    "propose_task": (AgentAddress, "Ask a discovered agent whether it can meet the original task and all requirements. The agent independently assesses and returns an offer or decline. Can retry after an error."),
    "confirm_offer": (AgentAddress, "Confirm this agent's stored offer over A2A. Only available after it offered to help."),
    "finish_search": (Finish, "Return found for a confirmed agent, or not_found after checking the whole roster and resolving matching candidates. An unconfirmed offer cannot count as success."),
    "ask_user": (Clarification, "Return a specific clarification question when task requirements are ambiguous or missing."),
}

TOOLS = [
    {"type": "function", "function": {"name": name, "description": description,
                                      "parameters": schema.model_json_schema()}}
    for name, (schema, description) in TOOL_SPECS.items()
]


@dataclass
class Negotiation:
    request: NegotiationRequest
    context_id: str
    status: str = "error"
    offer: NegotiationReply | None = None
    confirmation: NegotiationReply | None = None


class ManagerTools:
    """Per-search evidence. Only the LLM decides which public action runs next."""

    def __init__(self, request: SearchRequest, urls: tuple[str, ...], peer):
        self.request, self.peer = request, peer
        self.urls = dict.fromkeys(url.rstrip("/") for url in urls)
        self.cards: dict[str, AgentCard] = {}
        self.inspected: set[str] = set()
        self.discovery_errors: set[str] = set()
        self.required = normalize_capabilities(request.required_capabilities)
        self.explicit_requirements = bool(request.required_capabilities)
        self.negotiations: dict[str, Negotiation] = {}
        self.attempts: list[Attempt] = []
        self.actions: list[dict] = []
        self.finished: SearchResult | None = None

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
        handlers = {"discover_agent": self.discover_agent, "set_requirements": self.set_requirements,
                    "propose_task": self.propose_task, "confirm_offer": self.confirm_offer,
                    "finish_search": self.finish_search, "ask_user": self.ask_user}
        return await handlers[name](params)

    async def discover_agent(self, args: AgentAddress) -> dict:
        url = self.address(args.agent_url)
        self.inspected.add(url)
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
        if (self.explicit_requirements or self.negotiations) and set(required) != set(self.required):
            raise ValueError("Cannot change explicit requirements or requirements already sent in a proposal.")
        if not self.negotiations:
            self.required = required
        return {"required_capabilities": self.required}

    async def propose_task(self, args: AgentAddress) -> dict:
        url = self.address(args.agent_url)
        if url not in self.cards:
            raise ValueError("Discover this agent before proposing a task.")
        if not self.required:
            raise ValueError("Set the required capabilities before proposing a task.")
        card = self.cards[url]
        missing = set(self.required) - {skill.id for skill in card.skills}
        if missing:
            raise ValueError(f"Card lacks required capabilities: {', '.join(sorted(missing))}.")
        exchange = Negotiation(NegotiationRequest(action="propose", request_id=uuid4().hex,
            query=self.request.query, required_capabilities=self.required), uuid4().hex)
        self.negotiations[url] = exchange
        return await self.exchange(url, exchange, "proposal")

    async def confirm_offer(self, args: AgentAddress) -> dict:
        url = self.address(args.agent_url)
        exchange = self.negotiations.get(url)
        if url not in self.cards or exchange is None or exchange.offer is None:
            raise ValueError("An offer from a discovered agent is required before confirmation.")
        if exchange.confirmation is not None:
            return exchange.confirmation.model_dump()
        return await self.exchange(url, exchange, "confirmation")

    async def exchange(self, url: str, exchange: Negotiation, phase: str) -> dict:
        card = self.cards[url]
        request = exchange.request
        if phase == "confirmation":
            request = request.model_copy(update={"action": "confirm", "offer_id": exchange.offer.offer_id})
        try:
            reply = NegotiationReply.model_validate(await self.peer.send(card, request.model_dump(), exchange.context_id))
            self.validate_reply(reply, request, card)
            allowed = {"offered", "declined", "error"} if phase == "proposal" else {"confirmed", "declined", "error"}
            if reply.status not in allowed:
                raise ValueError(f"Unexpected {phase} status: {reply.status}")
            if reply.status == "offered":
                if not reply.offer_id:
                    raise ValueError("Agent returned an offer without an offer ID.")
                exchange.offer = reply
            if reply.status == "confirmed":
                if reply.offer_id != exchange.offer.offer_id:
                    raise ValueError("Agent confirmed a different offer")
                exchange.confirmation = reply
            exchange.status = reply.status
            self.attempts.append(Attempt(agent_name=card.name, agent_url=card.url, phase=phase,
                                         status=reply.status, detail=reply.reason))
            return reply.model_dump()
        except Exception as exc:
            exchange.status = "error"
            self.attempts.append(Attempt(agent_name=card.name, agent_url=card.url, phase=phase,
                                         status="error", detail=str(exc)))
            return {"error": str(exc), "agent_url": url, "retry_possible": True}

    @staticmethod
    def validate_reply(reply: NegotiationReply, request: NegotiationRequest, card: AgentCard):
        if reply.request_id != request.request_id or reply.agent_name != card.name:
            raise ValueError("Negotiation reply does not match the request or agent card")
        if reply.status in {"offered", "confirmed"}:
            if not set(request.required_capabilities).issubset(reply.capabilities):
                raise ValueError("Agent did not offer every required capability")
            if not set(reply.capabilities).issubset({skill.id for skill in card.skills}):
                raise ValueError("Agent offered capabilities absent from its card")

    async def finish_search(self, args: Finish) -> dict:
        if not self.required:
            raise ValueError("Set required capabilities or ask the user for clarification before finishing.")
        if args.status == "found":
            url = self.address(args.agent_url or "")
            exchange = self.negotiations.get(url)
            if url not in self.cards or exchange is None or exchange.confirmation is None:
                raise ValueError("Cannot report found without a confirmed A2A offer from that agent.")
            self.validate_reply(exchange.confirmation, exchange.request, self.cards[url])
            selected = SelectedAgent(name=self.cards[url].name, url=self.cards[url].url,
                capabilities=exchange.confirmation.capabilities, offer_id=exchange.confirmation.offer_id)
            self.finished = self.result("found",
                f"Found {selected.name} at {selected.url}. It confirmed: {', '.join(self.required)}.", selected)
        else:
            if args.agent_url is not None:
                raise ValueError("Use null for agent_url when reporting not_found.")
            unchecked = set(self.urls) - self.inspected
            if unchecked:
                raise ValueError(f"Inspect the remaining agents before reporting not_found: {sorted(unchecked)}")
            unresolved = []
            for url, card in self.cards.items():
                if set(self.required).issubset({s.id for s in card.skills}):
                    exchange = self.negotiations.get(url)
                    if exchange is None or exchange.status not in {"declined", "error"}:
                        unresolved.append(url)
            if unresolved:
                raise ValueError(f"Resolve matching agents before not_found; use found if one confirmed: {unresolved}")
            summary = "Could not find an agent that confirmed all requested capabilities among the configured agents."
            if self.discovery_errors or any(n.status == "error" for n in self.negotiations.values()):
                summary += " The search is incomplete because some agents or models returned errors; see attempts."
            self.finished = self.result("not_found", summary)
        return {"status": self.finished.status, "summary": self.finished.summary}

    async def ask_user(self, args: Clarification) -> dict:
        if not args.question.strip():
            raise ValueError("Provide a nonempty clarification question.")
        self.finished = self.result("input_required", args.question)
        return {"status": "input_required", "question": args.question}

    def result(self, status: str, summary: str, selected: SelectedAgent | None = None) -> SearchResult:
        return SearchResult(status=status, query=self.request.query, required_capabilities=self.required,
            selected_agent=selected, summary=summary, attempts=self.attempts,
            discovered_agents=[{"name": card.name, "url": card.url, "capabilities": [s.id for s in card.skills]}
                               for card in self.cards.values()], manager_actions=self.actions)
