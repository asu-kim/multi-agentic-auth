from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class DroneAgent(TaskAgent):
    """Publish warehouse drone capabilities and produce simulated task plans."""
    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("drone_agent_card.json"), url)
        super().__init__(card, llm, instructions=(
            "You are the warehouse drone agent. Your modeled capabilities are "
            "reach top shelf and carry light items. Produce a simulated retrieval or transport plan only. "
        ), simulation=True)
