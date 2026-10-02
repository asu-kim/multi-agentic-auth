import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    llm_url: str
    api_key: str
    manager_model: str
    subagent_model: str
    urls: dict[str, str]
    agent_urls: tuple[str, ...]
    llm_timeout: float
    a2a_timeout: float
    robot_available: bool

    @classmethod
    def from_env(cls):
        load_dotenv()
        llm_url = os.getenv("TOOL_LLM_URL", "http://127.0.0.1:11435/v1").rstrip("/")
        if not llm_url.endswith("/v1"):
            raise ValueError("TOOL_LLM_URL must end in /v1, e.g. http://127.0.0.1:11435/v1")
        urls = {
            role: os.getenv(f"{role.upper()}_URL", f"http://127.0.0.1:{port}/")
            for role, port in [("manager", 10000), ("language", 10001),
                               ("analytics", 10002), ("robot", 10003)]
        }
        roster = os.getenv("AGENT_URLS", ",".join(v for k, v in urls.items() if k != "manager"))
        agent_urls = tuple(dict.fromkeys(x.strip() for x in roster.split(",") if x.strip()))
        for url in [llm_url, *urls.values(), *agent_urls]:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"Invalid server URL: {url}")
        return cls(
            llm_url=llm_url, api_key=os.getenv("API_KEY", "ollama"),
            manager_model=os.getenv("MANAGER_MODEL", "gpt-oss:20b"),
            subagent_model=os.getenv("SUBAGENT_MODEL", "llama3.2:3b"),
            urls=urls, agent_urls=agent_urls,
            llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "180")),
            a2a_timeout=float(os.getenv("A2A_TIMEOUT_SECONDS", "900")),
            robot_available=os.getenv("ROBOT_AVAILABLE", "true").lower() in {"1", "true", "yes"},
        )
