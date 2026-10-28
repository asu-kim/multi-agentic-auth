import os
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv


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
        roster = os.getenv("AGENT_URLS", ",".join(v for k, v in urls.items() if k != "manager"))
        agent_urls = tuple(dict.fromkeys(x.strip() for x in roster.split(",") if x.strip()))
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
