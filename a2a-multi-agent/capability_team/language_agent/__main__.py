"""Start this agent: python -m capability_team.language_agent"""
from capability_team.common.server_runtime import build_a2a_app, create_model, run_server
from capability_team.common.settings import Settings
from capability_team.language_agent.language_agent import LanguageAgent
from capability_team.language_agent.language_agent_executor import LanguageAgentExecutor


def build_app(agent: LanguageAgent, http=None):
    return build_a2a_app(agent.card, LanguageAgentExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    agent = LanguageAgent(llm, url=settings.urls["language"])
    return build_app(agent, http)


if __name__ == "__main__":
    run_server("language", create_app)
