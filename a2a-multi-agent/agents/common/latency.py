"""Request-local monotonic timings. Persist metadata only, never keys or model text."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import json
import logging
import math
import os
from pathlib import Path
import socket
import tempfile
from time import perf_counter_ns
from uuid import uuid4

logger = logging.getLogger(__name__)
_current = ContextVar("latency_trace", default=None)


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
            row = summary.setdefault(event["name"], {"count": 0, "ok_count": 0, "total_ms": 0.0})
            row["count"] += 1
            row["ok_count"] += event["status"] == "ok"
            row["total_ms"] += event["duration_ms"]
        payload = {"schema": "latency/v1", "trace_id": self.trace_id,
                   "role": self.role, "started_at": self.started_at, "outcome": outcome,
                   "total_ms": elapsed, "host": socket.gethostname(), "pid": os.getpid(),
                   "metadata": self.metadata, "summary": summary,
                   "events": self.events, "worker_traces": self.worker_traces,
                   "warnings": self.warnings}
        directory = Path(os.getenv("LATENCY_OUTPUT_DIR", str(
            Path(__file__).resolve().parents[2] / "results/latency"))).expanduser().resolve()
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
