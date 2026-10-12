from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class AnalyticsAgent(TaskAgent):
    """Publish a card and handle explicit LLM task requests."""
    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("analytics_agent_card.json"), url)
        super().__init__(card, llm, instructions='Analyze only the supplied numerical data. Return the requested statistics or anomaly analysis with your method.', simulation=False)
