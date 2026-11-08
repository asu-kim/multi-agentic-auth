"""Request-local monotonic timings. Persist metadata only, never keys or model text."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps, lru_cache
from hashlib import sha256
import json
import logging
import math
import os
from pathlib import Path
import socket
import tempfile
from time import perf_counter_ns, time_ns, get_clock_info
from uuid import uuid4

logger = logging.getLogger(__name__)
_current = ContextVar("latency_trace", default=None)
_PROCESS_CLOCK_DOMAIN = "process:" + uuid4().hex


@lru_cache(maxsize=1)
def clock_domain():
    # Linux processes in the same boot/time namespace share CLOCK_MONOTONIC.
    # A process-only fallback safely supports in-process ASGI on other systems.
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        namespace = os.readlink("/proc/self/ns/time")
        identity = f"{boot_id}:{namespace}:{get_clock_info('perf_counter').implementation}"
        return "linux:" + sha256(identity.encode()).hexdigest()
    except OSError:
        return _PROCESS_CLOCK_DOMAIN


def capture_dispatch_timestamp():
    return {"monotonic_ns": perf_counter_ns(), "unix_ns": time_ns(),
            "clock_domain": clock_domain()}


def record_task_delivery(trace, request):
    sent, received = request.dispatch_timing, request._received_timing
    if sent is None or received is None:
        return
    shared = sent.clock_domain == received["clock_domain"]
    synchronized = os.getenv("LATENCY_CLOCKS_SYNCHRONIZED", "false").lower() in {"1", "true", "yes"}
    wall_delta_ms = (received["unix_ns"] - sent.unix_ns) / 1e6
    attributes = {"boundary": "a2a_send_to_executor_entry",
                  "sender_unix_ns": sent.unix_ns, "receiver_unix_ns": received["unix_ns"]}
    if shared:
        duration = (received["monotonic_ns"] - sent.monotonic_ns) / 1e6
        status = "ok"
        attributes["clock_basis"] = "shared_monotonic"
    else:
        attributes.update(clock_basis="wall_clock", wall_clock_delta_ms=wall_delta_ms,
                          clock_sync_assumed=synchronized)
        duration = wall_delta_ms if synchronized else None
        status = "estimated" if synchronized else "clock_sync_unverified"
    if duration is not None and duration < 0:
        attributes["invalid_delta_ms"] = duration
        duration, status = None, "clock_error"
    trace.events.append({"name": "a2a_task_delivery", "duration_ms": duration,
                         "status": status, "attributes": attributes})


def current_trace():
    return _current.get()


class LatencyTrace:
    def __init__(self, role, trace_id=None, **metadata):
        self.trace_id = trace_id or uuid4().hex
        self.role = role
        self.metadata = metadata
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started_ns = perf_counter_ns()
        self.events = []
        self.worker_traces = []
        self.warnings = []

    def record(self, name, start_ns, *, end_ns=None, status="ok", **attributes):
        end_ns = perf_counter_ns() if end_ns is None else end_ns
        event = {"name": name, "duration_ms": (end_ns - start_ns) / 1e6,
                 "start_offset_ms": (start_ns - self.started_ns) / 1e6,
                 "status": status, "attributes": attributes}
        self.events.append(event)
        return event

    def import_node(self, path, **attributes):
        # Read only the timing side channel; normal SST stdout can contain keys.
        try:
            events = json.loads(path.read_text())["events"]
            allowed = {"sst_session_key", "sst_connect", "sst_handshake", "sst_command_reply"}
            measured_key = False
            for event in events:
                value = event.get("duration_ms")
                if (event.get("name") not in allowed or isinstance(value, bool)
                        or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                    continue
                measured_key = measured_key or event["name"] == "sst_session_key"
                self.events.append({"name": event["name"], "duration_ms": value,
                                    "status": event.get("status", "ok"), "source": "node",
                                    "attributes": attributes})
            if not measured_key:
                self.warnings.append(f"No session-key callback timing for {attributes.get('resource')}; check IoTAuth module resolution or early termination.")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Aborted or custom command processes may not have timing data.
            self.warnings.append(f"SST timing data unavailable for {attributes.get('resource')}.")

    def finish(self, outcome):
        elapsed = (perf_counter_ns() - self.started_ns) / 1e6
        summary = {}
        for event in self.events:
            row = summary.setdefault(event["name"], {"count": 0, "ok_count": 0, "measured_count": 0, "total_ms": None})
            row["count"] += 1
            row["ok_count"] += event["status"] == "ok"
            value = event["duration_ms"]
            if value is not None:
                row["total_ms"] = (row["total_ms"] or 0.0) + value
                row["measured_count"] += 1
        payload = {"schema": "latency/v1", "trace_id": self.trace_id,
                   "role": self.role, "started_at": self.started_at, "outcome": outcome,
                   "total_ms": elapsed, "host": socket.gethostname(), "pid": os.getpid(),
                   "metadata": self.metadata, "summary": summary,
                   "events": self.events, "worker_traces": self.worker_traces,
                   "warnings": self.warnings}
        directory = Path(os.getenv("LATENCY_OUTPUT_DIR", str(
            Path(__file__).resolve().parents[2] / "robot_deploy/latency"))).expanduser().resolve()
        target = directory / f"{self.trace_id}_{self.role}_{uuid4().hex[:8]}.json"
        payload["file"] = str(target)
        temporary = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=directory, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                json.dump(payload, output, indent=2, ensure_ascii=False, allow_nan=False)
                output.write("\n")
            temporary.replace(target)
        except (OSError, ValueError) as exc:
            payload["file"] = None
            payload["write_error"] = str(exc)
            logger.error("Cannot save latency JSON: %s", exc)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return payload


@contextmanager
def measure(name, **attributes):
    start = perf_counter_ns()
    sample = {"status": "ok"}
    try:
        yield sample
    except BaseException as exc:
        sample["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        raise
    finally:
        trace = current_trace()
        if trace is not None:
            trace.record(name, start, status=sample["status"], **attributes)


def traced(role):
    """Create an independent trace even when ASGI calls share an asyncio context."""
    def decorate(method):
        @wraps(method)
        async def wrapped(self, request):
            if os.getenv("LATENCY_ENABLED", "true").lower() in {"0", "false", "no"}:
                return await method(self, request)
            trace = LatencyTrace(role, getattr(request, "trace_id", None),
                                 agent=self.card.name)
            if role == "worker":
                record_task_delivery(trace, request)
            token = _current.set(trace)
            result, outcome = None, "error"
            try:
                with measure(f"{role}_execution") as sample:
                    result = await method(self, request)
                    outcome = result.status
                    if outcome == "error":
                        sample["status"] = "error"
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            finally:
                try:
                    payload = trace.finish(outcome)
                finally:
                    _current.reset(token)
            return result.model_copy(update={"latency": payload})
        return wrapped
    return decorate
