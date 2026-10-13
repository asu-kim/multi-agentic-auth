"""Robot-owned IoTAuth connection; retained until disconnect or server shutdown."""
import asyncio
import os
import re
from pathlib import Path


class RobotTools:
    def __init__(self):
        self.process = None
        self.reader = None
        self.connected = False
        self.lock = asyncio.Lock()

    async def establish_secure_communication(self) -> dict:
        async with self.lock:
            if self.connected and self.process and self.process.returncode is None:
                return {"status": "connected", "reused": True}
            await self.close()
            directory = Path(os.getenv("IOTAUTH_ENTITY_DIR", str(
                Path(__file__).resolve().parents[3] / "iotauth/entity/node/example_entities")))
            config = os.getenv("IOTAUTH_ROBOT_CONFIG", "configs/net1/robotAgent.config")
            target = os.getenv("IOTAUTH_ROBOT_RESOURCE", "net1.resource")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", target):
                return {"status": "error", "error": "Invalid IOTAUTH_ROBOT_RESOURCE."}
            if not (directory / config).is_file():
                return {"status": "error", "error": f"Robot config not found: {directory / config}"}
            ready = asyncio.get_running_loop().create_future()
            sent = False

            async def read_events():
                nonlocal sent
                reading_parameters = False
                try:
                    async for line in self.process.stdout:
                        text = line.decode("utf-8", errors="replace").strip()
                        if text.startswith("current parameters:"):
                            reading_parameters = True
                        if reading_parameters and text.endswith("}") and not sent:
                            self.process.stdin.write(f"initComm {target}\n".encode())
                            sent = True
                            await self.process.stdin.drain()
                        elif text == "switching to IN_COMM" and sent:
                            self.connected = True
                            if not ready.done():
                                ready.set_result({"status": "connected", "target": target})
                        elif (text == "Handler: secure connection with the server closed."
                              or text.startswith("Handler: Error in secure comm - details:")
                              or text.startswith("cannot find communication server named ")):
                            self.connected = False
                            if not ready.done():
                                ready.set_result({"status": "error", "error": text})
                except Exception as exc:
                    if not ready.done():
                        ready.set_result({"status": "error", "error": str(exc)})
                finally:
                    self.connected = False
                    if not ready.done():
                        ready.set_result({"status": "error", "error": "Node exited before confirming secure communication."})

            try:
                timeout = float(os.getenv("IOTAUTH_TIMEOUT_SECONDS", "30"))
                if timeout <= 0:
                    raise ValueError("IOTAUTH_TIMEOUT_SECONDS must be positive.")
                async with asyncio.timeout(timeout):
                    self.process = await asyncio.create_subprocess_exec(
                        os.getenv("IOTAUTH_NODE", "node"), "user.js", config,
                        cwd=directory, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                    self.reader = asyncio.create_task(read_events())
                    result = await ready
                if result["status"] != "connected":
                    await self.close()
                return result
            except asyncio.CancelledError:
                await self.close()
                raise
            except (OSError, ValueError, TimeoutError) as exc:
                await self.close()
                return {"status": "error", "error": str(exc) or "Secure communication timed out.",
                        "command_sent": sent}

    async def close(self):
        self.connected = False
        if self.process is not None and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader is not None:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.process = self.reader = None
