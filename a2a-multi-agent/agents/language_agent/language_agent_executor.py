from agents.common.negotiation_executor import NegotiationExecutor
from agents.language_agent.language_agent import LanguageAgent


class LanguageAgentExecutor(NegotiationExecutor):
    """A2A request adapter for LanguageAgent."""

    def __init__(self, agent: LanguageAgent):
        super().__init__(agent)
