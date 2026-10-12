"""Drive the IoTAuth interactive Node entity without a shell or log scraping of objects."""
import asyncio
import json
import os
import re
from pathlib import Path


class IoTAuthDelegate:
    def __init__(self):
        self.directory = Path(os.getenv("IOTAUTH_ENTITY_DIR", str(
            Path(__file__).resolve().parents[3] / "iotauth/entity/node/example_entities")))
        self.config = os.getenv("IOTAUTH_MANAGER_CONFIG", "configs/net1/managerAgent.config")
        self.node = os.getenv("IOTAUTH_NODE", "node")
        self.timeout = float(os.getenv("IOTAUTH_TIMEOUT_SECONDS", "30"))
        if self.timeout <= 0:
            raise ValueError("IOTAUTH_TIMEOUT_SECONDS must be positive.")
        # Trusted deployment mapping: never derive authorization subjects from card text.
        subjects = json.loads(os.getenv("IOTAUTH_AGENT_GROUPS", "{}"))
        if not isinstance(subjects, dict) or any(
            not isinstance(url, str) or not isinstance(group, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", group)
            for url, group in subjects.items()
        ):
            raise ValueError("IOTAUTH_AGENT_GROUPS must map agent URLs to single-token IoTAuth group names.")
        self.subjects = {url.rstrip("/"): group for url, group in subjects.items()}

    async def delegate(self, agent_url: str) -> dict:
        subject = self.subjects.get(agent_url.rstrip("/"))
        if subject is None:
            return {"status": "error", "error": "No IoTAuth group is configured for the selected agent.",
                    "agent_url": agent_url, "request_sent": False}
        config = self.directory / self.config
        if not config.is_file():
            return {"status": "error", "error": f"Entity manager config not found: {config}"}
        process = None
        sent = False
        try:
            async with asyncio.timeout(self.timeout):
                process = await asyncio.create_subprocess_exec(
                    self.node, "user.js", self.config, "--privilege-json",
                    cwd=self.directory, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                policy = None
                received = finished = False
                while line := await process.stdout.readline():
                    text = line.decode("utf-8", errors="replace").strip()
                    if text == "IOTAUTH_READY" and not sent:
                        process.stdin.write(f"delegateAuthority {subject} Resources 1*day\n".encode("utf-8"))
                        sent = True
                        await process.stdin.drain()
                    elif text.startswith("IOTAUTH_ERROR "):
                        return {"status": "error", "error": text.removeprefix("IOTAUTH_ERROR "),
                                "request_sent": sent}
                    elif text.startswith("IOTAUTH_PRIVILEGE ") and sent:
                        response = json.loads(text.removeprefix("IOTAUTH_PRIVILEGE "))
                        policy = response.get("communicationPolicy") if isinstance(response, dict) else None
                    elif text == "received privilege response!" and sent:
                        received = True
                    elif text == "Finished privilege request" and sent:
                        finished = True
                    if received and finished and policy is not None:
                        grant = policy.get("newCommunicationPolicy", {}) if isinstance(policy, dict) else {}
                        if (not isinstance(policy, dict) or policy.get("action") != "DelegationGrant"
                                or not isinstance(grant, dict) or grant.get("IsDelegated") != 1):
                            return {"status": "error", "error": "IoTAuth did not confirm a DelegationGrant.",
                                    "request_sent": sent}
                        return {"status": "delegated", "subject": subject, "object": "Resources",
                                "validity": "1*day", "communication_policy": policy,
                                "confirmation": ["received privilege response!", "Finished privilege request"]}
                return {"status": "error", "error": "Node entity exited without a confirmed privilege response.",
                        "request_sent": sent}
        except (OSError, TimeoutError, ValueError) as exc:
            return {"status": "error", "error": str(exc) or "IoTAuth delegation timed out.",
                    "request_sent": sent}
        finally:
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    process.kill()
                    await process.wait()
