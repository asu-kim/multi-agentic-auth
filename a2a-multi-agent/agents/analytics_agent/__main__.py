"""Start this agent: python -m agents.analytics_agent"""
from agents.common.server_runtime import build_a2a_app, create_model, run_server
from agents.common.task_executor import TaskExecutor
from agents.common.settings import Settings
from agents.analytics_agent.analytics_agent import AnalyticsAgent


def build_app(agent: AnalyticsAgent, http=None):
    return build_a2a_app(agent.card, TaskExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    return build_app(AnalyticsAgent(llm, url=settings.urls["analytics"]), http)


if __name__ == "__main__":
    run_server("analytics", create_app)
