from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class RobotAgent(TaskAgent):
    """Publish a card and handle explicit LLM task requests."""
    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("robot_agent_card.json"), url)
        super().__init__(card, llm, instructions='Return a proposed step-by-step simulated plan within the card limits.', simulation=True)
