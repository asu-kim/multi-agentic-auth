"""Start this agent: python -m agents.language_agent"""
from agents.common.server_runtime import build_a2a_app, create_model, run_server
from agents.common.task_executor import TaskExecutor
from agents.common.settings import Settings
from agents.language_agent.language_agent import LanguageAgent


def build_app(agent: LanguageAgent, http=None):
    return build_a2a_app(agent.card, TaskExecutor(agent), http)


def create_app(settings: Settings):
    http, llm = create_model(settings, settings.subagent_model)
    return build_app(LanguageAgent(llm, url=settings.urls["language"]), http)


if __name__ == "__main__":
    run_server("language", create_app)
