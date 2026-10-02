"""Start this agent: python -m capability_team.analytics_agent"""
from capability_team.common.server_runtime import build_a2a_app, create_model, run_server
from capability_team.common.settings import Settings
from capability_team.analytics_agent.analytics_agent import AnalyticsAgent
from capability_team.analytics_agent.analytics_agent_executor import AnalyticsAgentExecutor


def build_app(agent: AnalyticsAgent, http=None):
    return build_a2a_app(agent.card, AnalyticsAgentExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    agent = AnalyticsAgent(llm, url=settings.urls["analytics"])
    return build_app(agent, http)


if __name__ == "__main__":
    run_server("analytics", create_app)
