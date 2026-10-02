from pathlib import Path

from a2a.types import AgentCard


def load_agent_card(path: Path, url: str | None = None) -> AgentCard:
    """Load one agent's own JSON card; configuration may override its endpoint."""
    card = AgentCard.model_validate_json(path.read_text(encoding="utf-8"))
    if url is not None:
        card = card.model_copy(update={"url": url})
    return card
