from pathlib import Path
from agents.common.agent_card_loader import load_agent_card
from agents.common.task_agent import TaskAgent


class ForkliftAgent(TaskAgent):
    """Publish forklift capabilities and execute shared warehouse delivery tasks."""
    worker = "FORKLIFT"
    default_config = "forkliftAgent"

    def __init__(self, llm=None, *, url: str | None = None):
        card = load_agent_card(Path(__file__).with_name("forklift_agent_card.json"), url)
        super().__init__(card, llm, instructions=(
            "You are the warehouse forklift agent. Your modeled capabilities are "
            "reach middle shelf and carry heavy items. "
            "Use the shared delivery action for retrieval through a gate into the secure zone. "
            "For other physical tasks, return a simulated plan. "
        ), simulation=True)
