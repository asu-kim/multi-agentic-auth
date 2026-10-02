from agents.common.negotiation_executor import NegotiationExecutor
from agents.analytics_agent.analytics_agent import AnalyticsAgent


class AnalyticsAgentExecutor(NegotiationExecutor):
    """A2A request adapter for AnalyticsAgent."""

    def __init__(self, agent: AnalyticsAgent):
        super().__init__(agent)
