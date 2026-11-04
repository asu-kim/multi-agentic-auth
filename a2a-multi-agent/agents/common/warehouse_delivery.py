"""Verify the protected actions for a simulated delivery using the worker's IoTAuth identity."""
import asyncio
import json
import math
import os
from pathlib import Path
from uuid import uuid4

import httpx

from agents.common.task_models import ResourceEndpoint, TaskResult
from agents.manager_agent.resource_catalog import load_resource_catalog


def delivery_groups():
    return (os.getenv("IOTAUTH_ITEM_GROUP", "TargetItem"), os.getenv("IOTAUTH_GATE_GROUP", "Gate"))


def delivery_endpoints(entities):
    endpoints = {}
    for group in delivery_groups():
        matches = [e for e in entities if e.get("group") == group]
        if len(matches) != 1:
            raise ValueError(f"Expected one target endpoint for {group}, found {len(matches)}.")
        endpoints[group] = ResourceEndpoint(host=matches[0].get("host"), port=matches[0].get("port"))
    return endpoints


def catalog_endpoints(catalog):
    return delivery_endpoints([{**entity, "group": row["group"]}
                               for row in catalog["resources"] for entity in row["entities"]])


async def deliver(agent_name, worker, default_config, delegated_groups, endpoints=None):
    item, gate = delivery_groups()
    def error(message):
        return TaskResult(agent_name=agent_name, status="error", output=message)

    if item == gate or not item or not gate:
        return error("Configure distinct item and gate resource groups.")
    if delegated_groups is not None and not {item, gate}.issubset(delegated_groups):
        return error("Delivery requires confirmed delegation for both the item and the gate.")
    directory = Path(os.getenv("IOTAUTH_ENTITY_DIR", str(
        Path(__file__).resolve().parents[3] / "iotauth/entity/node/example_entities")))
    config = os.getenv(f"IOTAUTH_{worker}_CONFIG", f"configs/net1/{default_config}.config")
    script = Path(__file__).with_name("resource_command.js").resolve()
    try:
        if endpoints is None:
            if os.getenv("IOTAUTH_GRAPH_FILE", "").strip():
                endpoints = catalog_endpoints(load_resource_catalog())
            else:
                info = json.loads((directory / config).read_text())
                endpoints = delivery_endpoints(info.get("targetServerInfoList") or [])
        if not {item, gate}.issubset(endpoints):
            raise ValueError("Delivery requires target endpoints for both the item and the gate.")
    except (OSError, ValueError) as exc:
        return error(str(exc))
    simulator = os.getenv("GAZEBO_DELIVERY_URL", "").strip().rstrip("/")
    run_id = uuid4().hex

    async def animate(action):
        if not simulator:
            return
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            response = await client.post(simulator + "/action",
                                         json={"action": action, "run_id": run_id})
            response.raise_for_status()
            expected_stage = {"begin": "at_item", "pickup": "at_gate", "finish": "completed"}
            if action in expected_stage and response.json().get("stage") != expected_stage[action]:
                raise ValueError(f"Gazebo did not confirm {action}.")

    steps = [(item, "RETRIEVE_ITEM", "retrieved"), (gate, "OPEN_GATE", "open")]
    confirmed = []
    completed = False
    try:
        timeout = float(os.getenv("IOTAUTH_TIMEOUT_SECONDS", "30"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid IOTAUTH_TIMEOUT_SECONDS")
        await animate("begin")
        for group, command, expected in steps:
            proc = await asyncio.create_subprocess_exec(
                os.getenv("IOTAUTH_NODE", "node"), str(script), config, group, endpoints[group].host, str(endpoints[group].port), command,
                cwd=directory, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try:
                async with asyncio.timeout(timeout):
                    code = await proc.wait()
            finally:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
            if code:
                detail = {2: "invalid endpoint", 3: "unexpected secure reply"}.get(code, "secure command failed")
                raise ValueError(f"{command} on {group}: {detail}")
            confirmed.append(expected)
            await animate("pickup" if command == "RETRIEVE_ITEM" else "finish")
        completed = True
    except (OSError, ValueError, TimeoutError, httpx.HTTPError) as exc:
        return error(f"Delivery failed: {str(exc) or 'timed out'}. Confirmed replies: {confirmed}. "
                     + ("Inspect the simulator and reset the scene before retrying." if simulator else ""))
    finally:
        if simulator and not completed:
            # A timeout or cancellation may leave an animation running on the Mac.
            try:
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    await asyncio.shield(client.post(simulator + "/action",
                                                     json={"action": "abort", "run_id": run_id}))
            except (httpx.HTTPError, asyncio.CancelledError):
                pass
    placement = ("Gazebo confirmed delivery and placement in the secure zone."
                 if simulator else "Placement in the safe zone is simulated.")
    return TaskResult(agent_name=agent_name, status="simulated",
                      output=f"{item} replied retrieved and {gate} replied open. {placement}")
