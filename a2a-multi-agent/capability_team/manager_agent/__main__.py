"""Start this agent: python -m capability_team.manager_agent"""
from capability_team.common.a2a_client import A2APeer
from capability_team.common.server_runtime import build_a2a_app, create_model, run_server
from capability_team.common.settings import Settings
from capability_team.manager_agent.manager_agent import ManagerAgent
from capability_team.manager_agent.manager_agent_executor import ManagerAgentExecutor


def build_app(agent: ManagerAgent, http=None):
    return build_a2a_app(agent.card, ManagerAgentExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.manager_model)
    agent = ManagerAgent(settings.agent_urls, A2APeer(http), llm, url=settings.urls["manager"])
    return build_app(agent, http)


if __name__ == "__main__":
    run_server("manager", create_app)
