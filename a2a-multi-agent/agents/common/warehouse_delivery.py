"""Verify the protected actions for a simulated delivery using the worker's IoTAuth identity."""
import asyncio
import json
import math
import logging
import os
from pathlib import Path
from uuid import uuid4
from tempfile import TemporaryDirectory
from agents.common.latency import current_trace, measure

import httpx

from agents.common.task_models import ResourceEndpoint, TaskResult
from agents.manager_agent.resource_catalog import load_resource_catalog



logger = logging.getLogger(__name__)


async def wait_with_status(proc, telemetry, agent_name, group):
    """Show metadata-only milestones on the worker's local terminal."""
    reported = set()
    def observe():
        try:
            events = json.loads(telemetry.read_text()).get("events", [])
            for event in events:
                name = event.get("name")
                if event.get("status") != "ok" or name in reported:
                    continue
                if name == "sst_session_key":
                    logger.info("STATUS | %s | Session key received: %s", agent_name, group)
                    reported.add(name)
                elif name == "sst_connect":
                    logger.info("STATUS | %s | Secure connection established: %s", agent_name, group)
                    reported.add(name)
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    waiter = asyncio.create_task(proc.wait())
    try:
        while not waiter.done():
            observe()
            await asyncio.wait({waiter}, timeout=0.1)
        observe()
        return await waiter
    finally:
        if not waiter.done():
            waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


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

    async def animate_impl(action):
        if not simulator:
            return
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            response = await client.post(simulator + "/action",
                                         json={"action": action, "run_id": run_id})
            response.raise_for_status()
            expected_stage = {"begin": "at_item", "pickup": "at_gate", "finish": "completed"}
            if action in expected_stage and response.json().get("stage") != expected_stage[action]:
                raise ValueError(f"Gazebo did not confirm {action}.")

    async def animate(action):
        if simulator:
            with measure("gazebo_action", action=action):
                await animate_impl(action)

    steps = [(item, "RETRIEVE_ITEM", "retrieved"), (gate, "OPEN_GATE", "open")]
    confirmed = []
    completed = False
    try:
        timeout = float(os.getenv("IOTAUTH_TIMEOUT_SECONDS", "30"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Invalid IOTAUTH_TIMEOUT_SECONDS")
        logger.info("STATUS | %s | Delivery task started", agent_name)
        await animate("begin")
        for group, command, expected in steps:
            with TemporaryDirectory(prefix="sst-latency-") as telemetry_dir:
                telemetry = Path(telemetry_dir) / "timings.json"
                trace = current_trace()
                env = dict(os.environ)
                env["IOTAUTH_LATENCY_FILE"] = str(telemetry)
                with measure("sst_resource_command", resource=group, command=command) as timing:
                    proc = await asyncio.create_subprocess_exec(
                        os.getenv("IOTAUTH_NODE", "node"), str(script), config, group,
                        endpoints[group].host, str(endpoints[group].port), command,
                        cwd=directory, env=env, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL)
                    try:
                        async with asyncio.timeout(timeout):
                            code = await wait_with_status(proc, telemetry, agent_name, group)
                        if code:
                            timing["status"] = "error"
                    finally:
                        if proc.returncode is None:
                            try:
                                proc.kill()
                            except ProcessLookupError:
                                pass
                            await proc.wait()
                        if trace is not None:
                            trace.import_node(telemetry, resource=group, command=command)
            if code:
                detail = {2: "invalid endpoint", 3: "unexpected secure reply"}.get(code, "secure command failed")
                raise ValueError(f"{command} on {group}: {detail}")
            confirmed.append(expected)
            logger.info("STATUS | %s | %s acknowledged by %s", agent_name, command, group)
            await animate("pickup" if command == "RETRIEVE_ITEM" else "finish")
        completed = True
        if simulator:
            logger.info("STATUS | %s | Gate opening confirmed in Gazebo", agent_name)
            logger.info("STATUS | %s | Item placed in the secure zone (Gazebo)", agent_name)
        else:
            logger.info("STATUS | %s | Secure replies confirmed; physical placement was not verified", agent_name)
    except (OSError, ValueError, TimeoutError, httpx.HTTPError) as exc:
        logger.error("STATUS | %s | Delivery failed: %s", agent_name, str(exc) or "timed out")
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
