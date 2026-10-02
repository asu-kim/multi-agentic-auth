from pathlib import Path

from agents.common.agent_card_loader import load_agent_card
from agents.common.negotiating_agent import NegotiatingAgent


class LanguageAgent(NegotiatingAgent):
    """Independent language agent; its adjacent JSON card defines its abilities and limits."""

    def __init__(self, llm, *, url: str | None = None, available: bool = True,
                 offer_ttl: float = 600):
        card = load_agent_card(Path(__file__).with_name("language_agent_card.json"), url)
        super().__init__(card, llm, available=available, offer_ttl=offer_ttl)
