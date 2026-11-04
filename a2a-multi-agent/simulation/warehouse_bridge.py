#!/usr/bin/env python3
"""Local Gazebo choreography for SST delivery. Run beside warehouse.sdf on the Mac."""
import argparse
import json
import math
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Warehouse:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.run_id = None
        self.stage = "idle"
        self.y = -5.5
        self.box_y = -2.65
        self.box_z = 0.3

    def gz(self, *args):
        if self.cancel.is_set():
            raise RuntimeError("Delivery aborted. Reset before starting another delivery.")
        result = subprocess.run(["gz", *args], capture_output=True, text=True, timeout=8)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gz failed")
        return result.stdout

    def poses(self):
        request = (
            'pose { name: "forklift" position { x: 1 y: %f z: 0 } '
            'orientation { z: 0.7071067811865476 w: 0.7071067811865476 } } '
            'pose { name: "cargo_box" position { x: 1 y: %f z: %f } '
            'orientation { w: 1 } }' % (self.y, self.box_y, self.box_z)
        )
        reply = self.gz("service", "-s", "/world/warehouse_demo/set_pose_vector",
                        "--reqtype", "gz.msgs.Pose_V", "--reptype", "gz.msgs.Boolean",
                        "--timeout", "5000", "--req", request)
        if not re.search(r"data:\s*true", reply):
            raise RuntimeError("Gazebo did not confirm model poses. Start warehouse.sdf with -r.")

    def move(self, destination, carrying=False):
        start = self.y
        duration = abs(destination - start) / 0.55
        began = time.monotonic()
        while True:
            fraction = min(1.0, (time.monotonic() - began) / max(duration, 0.01))
            self.y = start + (destination - start) * fraction
            if carrying:
                self.box_y = self.y + 0.85
            self.poses()
            if fraction >= 1:
                break
            time.sleep(0.08)

    def lift(self, height):
        start = self.box_z
        began = time.monotonic()
        while True:
            fraction = min(1.0, (time.monotonic() - began) / 1.5)
            self.box_z = start + (height - start) * fraction
            self.poses()
            if fraction >= 1:
                break
            time.sleep(0.08)

    def gate_angle(self):
        state = self.gz("topic", "-e", "-n", "1", "-t", "/secure_door/state")
        if 'name: "hinge"' not in state or "axis1 {" not in state:
            raise RuntimeError("No hinge state received from Gazebo.")
        axis = state.split("axis1 {", 1)[1]
        match = re.search(r"\bposition:\s*([-+0-9.eE]+)", axis)
        angle = float(match.group(1)) if match else 0.0
        if not math.isfinite(angle):
            raise RuntimeError("Invalid hinge position.")
        return angle

    def wait_gate(self, opened):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            angle = self.gate_angle()
            if (opened and angle >= 1.4) or (not opened and abs(angle) < 0.05):
                return
            time.sleep(0.2)
        raise RuntimeError("Door did not reach the required angle.")

    def reset(self):
        self.cancel.clear()
        self.gz("topic", "-t", "/secure_door/cmd_pos", "-m", "gz.msgs.Double", "-p", "data: 0")
        self.wait_gate(False)
        self.y, self.box_y, self.box_z = -5.5, -2.65, 0.3
        self.poses()
        self.run_id, self.stage = None, "idle"

    def execute(self, action, run_id):
        if action == "abort":
            if not run_id or self.run_id != run_id:
                raise ValueError("Task ID does not match.")
            self.cancel.set()
            self.stage = "error"
            return
        if not self.lock.acquire(blocking=False):
            raise ValueError("Another animation is running.")
        try:
            if action == "reset":
                self.reset()
                return
            if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
                raise ValueError("A 32-character task ID is required.")
            if action == "begin":
                if self.stage != "idle":
                    raise ValueError("Reset the scene before another delivery.")
                self.run_id = run_id
            elif self.run_id != run_id:
                raise ValueError("Task ID does not match.")
            expected = {"begin": "idle", "pickup": "at_item", "finish": "at_gate"}
            if action not in expected or self.stage != expected[action]:
                raise ValueError("Action is out of order.")
            self.stage = "moving"
            try:
                if action == "begin":
                    self.move(-3.5)
                    self.stage = "at_item"
                elif action == "pickup":
                    self.lift(0.72)
                    self.move(-1.4, carrying=True)
                    self.stage = "at_gate"
                else:
                    self.wait_gate(True)
                    self.move(2.5, carrying=True)
                    self.lift(0.3)
                    self.move(1.6)
                    self.stage = "completed"
                if self.cancel.is_set():
                    raise RuntimeError("Delivery aborted.")
            except Exception:
                self.stage = "error"
                raise
        finally:
            self.lock.release()

    def status(self):
        return {"stage": self.stage, "run_id": self.run_id,
                "forklift_y": self.y, "box_y": self.box_y, "box_z": self.box_z}


def serve(port):
    scene = Warehouse()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, payload):
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            self.reply(200 if self.path == "/status" else 404, scene.status())

        def do_POST(self):
            if self.path != "/action":
                self.reply(404, {"error": "Unknown path"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("Invalid request size")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("Expected a JSON object")
                action = request.get("action")
                if not isinstance(action, str):
                    raise ValueError("Expected an action string")
                scene.execute(action, request.get("run_id"))
                print(json.dumps({"event": action, "time": time.time(), **scene.status()}), flush=True)
                self.reply(200, scene.status())
            except ValueError as exc:
                self.reply(409, {"error": str(exc), **scene.status()})
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.reply(503, {"error": str(exc), **scene.status()})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Warehouse bridge listening on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        scene.cancel.set()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=21200)
    serve(parser.parse_args().port)
