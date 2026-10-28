from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class RoverAgent(TaskAgent):
    """Publish warehouse rover capabilities and produce simulated task plans."""
    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("rover_agent_card.json"), url)
        super().__init__(card, llm, instructions=(
            "You are the warehouse rover agent. Your modeled capabilities are "
            "reach bottom shelf and carry light items. Produce a simulated retrieval or transport plan only. "
        ), simulation=True)
