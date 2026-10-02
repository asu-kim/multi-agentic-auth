from capability_team.common.negotiation_executor import NegotiationExecutor
from capability_team.analytics_agent.analytics_agent import AnalyticsAgent


class AnalyticsAgentExecutor(NegotiationExecutor):
    """A2A request adapter for AnalyticsAgent."""

    def __init__(self, agent: AnalyticsAgent):
        super().__init__(agent)
