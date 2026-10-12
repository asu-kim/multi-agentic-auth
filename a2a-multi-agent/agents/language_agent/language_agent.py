from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class LanguageAgent(TaskAgent):
    """Publish a card and handle explicit LLM task requests."""
    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("language_agent_card.json"), url)
        super().__init__(card, llm, instructions='Translate or summarize the supplied written text. Return the actual translated text or summary.', simulation=False)
