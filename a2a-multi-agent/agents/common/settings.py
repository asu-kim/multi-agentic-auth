import os
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv


def load_agent_groups() -> dict[str, str]:
    """Read trusted A2A address-to-IoTAuth-group configuration."""
    try:
        groups = json.loads(os.getenv("IOTAUTH_AGENT_GROUPS", "{}"))
    except ValueError as exc:
        raise ValueError("IOTAUTH_AGENT_GROUPS must be a JSON object mapping agent URLs to groups.") from exc
    if not isinstance(groups, dict):
        raise ValueError("IOTAUTH_AGENT_GROUPS must map agent URLs to single-token IoTAuth group names.")
    normalized = {}
    for url, group in groups.items():
        if (not isinstance(url, str) or not isinstance(group, str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", group)):
            raise ValueError("IOTAUTH_AGENT_GROUPS must map agent URLs to single-token IoTAuth group names.")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"Invalid agent URL in IOTAUTH_AGENT_GROUPS: {url}")
        address = url.rstrip("/")
        if address in normalized and normalized[address] != group:
            raise ValueError(f"Conflicting IoTAuth groups for agent URL: {address}")
        normalized[address] = group
    return normalized


@dataclass(frozen=True)
class Settings:
    llm_url: str
    api_key: str
    manager_model: str
    urls: dict[str, str]
    agent_urls: tuple[str, ...]
    llm_timeout: float
    a2a_timeout: float
    subagent_model: str = "llama3.2:3b"
    manager_max_turns: int = 16
    manager_timeout: float = 600
    local_agent_modules: tuple[str, ...] = ("agents.manager_agent",)

    @classmethod
    def from_env(cls):
        load_dotenv()
        llm_url = os.getenv("TOOL_LLM_URL", "http://127.0.0.1:11435/v1").rstrip("/")
        if not llm_url.endswith("/v1"):
            raise ValueError("TOOL_LLM_URL must end in /v1, e.g. http://127.0.0.1:11435/v1")
        urls = {
            role: os.getenv(f"{role.upper()}_URL", f"http://127.0.0.1:{port}/")
            for role, port in [("manager", 10000), ("drone", 10001),
                               ("rover", 10002), ("forklift", 10003)]
        }
        groups = load_agent_groups()
        roster = os.getenv("AGENT_URLS")
        if roster is not None:
            # Optional explicit subset for deployments that restrict discovery.
            agent_urls = tuple(dict.fromkeys(x.strip() for x in roster.split(",") if x.strip()))
            missing = [url for url in agent_urls if url.rstrip("/") not in groups]
            if groups and missing:
                raise ValueError(f"AGENT_URLS entries are missing from IOTAUTH_AGENT_GROUPS: {missing}")
        elif groups:
            agent_urls = tuple(url for url in groups if url != urls["manager"].rstrip("/"))
        else:
            agent_urls = tuple(v for k, v in urls.items() if k != "manager")
        for url in [llm_url, *urls.values(), *agent_urls]:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Invalid server URL: {url}")
        modules = json.loads(os.getenv("LOCAL_AGENT_MODULES", '["agents.manager_agent"]'))
        if (not isinstance(modules, list) or not modules or
                any(not isinstance(m, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", m) for m in modules)
                or len(modules) != len(set(modules))):
            raise ValueError("LOCAL_AGENT_MODULES must be a nonempty JSON list of unique Python module names.")
        manager_max_turns = int(os.getenv("MANAGER_MAX_TURNS", "16"))
        manager_timeout = float(os.getenv("MANAGER_TIMEOUT_SECONDS", "600"))
        if manager_max_turns < 1 or manager_timeout <= 0:
            raise ValueError("MANAGER_MAX_TURNS and MANAGER_TIMEOUT_SECONDS must be positive.")
        return cls(
            llm_url=llm_url, api_key=os.getenv("API_KEY", "ollama"),
            manager_model=os.getenv("MANAGER_MODEL", "gpt-oss:20b"),
            subagent_model=os.getenv("SUBAGENT_MODEL", "llama3.2:3b"),
            urls=urls, agent_urls=agent_urls,
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
            a2a_timeout=float(os.getenv("A2A_TIMEOUT_SECONDS", "900")),
            manager_max_turns=manager_max_turns, manager_timeout=manager_timeout,
            local_agent_modules=tuple(modules),
        )
