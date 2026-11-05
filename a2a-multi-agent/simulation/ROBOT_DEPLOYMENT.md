# Forklift Agent on ROSMASTER

This setup runs the Forklift Agent on the robot. All motion, gate animation, and placement remain in Gazebo on the Mac. ROS motor control is not used. The robot's local terminal shows English status messages.

| Host | Services |
|---|---|
| Ubuntu server (`10.218.101.248`) | Auth101, LLM service, Manager and optional Drone/Rover agents |
| Mac (`100.101.175.20`) | Gazebo, warehouse bridge, Gate, TargetItem, SSH tunnels |
| ROSMASTER (`100.101.153.11`) | Forklift Agent, Node SST client, local terminal display |

The robot address comes from the supplied Tailscale screenshot. Confirm that it is online before connecting. Replace `ROBOT_USER` with the robot's SSH login. Keep the robot awake and connected during the task.

## 1. Install the worker code

Put the current repository at `~/multi-agentic-auth` on the robot. Use Python 3.12 or later in a separate virtual environment, and a working Node.js runtime. Run `python -m pip install -e .` from `a2a-multi-agent` and `npm install` from `iotauth/entity/node`. The robot does not need Gazebo or a local LLM for the explicit `deliver_to_safe_zone` action. The tunnel below also makes the server LLM available for natural-language requests.

## 2. Copy the Forklift credentials

The checked configuration uses `usePermanentDistKey: true`. It needs these two files from the Auth deployment that provisioned `net1.forkliftAgent`:

- `Net1.forkliftAgentCipherKey.key`
- `Net1.forkliftAgentMacKey.key`

On the Mac:

```bash
cd ~/multi-agentic-auth/iotauth
scp 'sunyoung@10.218.101.248:~/multi-agentic-auth/iotauth/entity/credentials/keys/net1/Net1.gate*Key.key' entity/credentials/keys/net1
scp 'sunyoung@10.218.101.248:~/multi-agentic-auth/iotauth/entity/credentials/keys/net1/Net1.targetItem*Key.key' entity/credentials/keys/net1

scp 'sunyoung@10.218.101.248:~/multi-agentic-auth/iotauth/entity/credentials/keys/net1/Net1.forkliftAgent*Key.key' entity/credentials/keys/net1
scp entity/credentials/keys/net1/Net1.forkliftAgent*Key.key \
  jetson@100.101.153.11:~/multi-agentic-auth/iotauth/entity/credentials/keys/net1/

```

Keep the matching `configs/net1/forkliftAgent.config`. In the current permanent distribution-key mode, the loader does not read a client private key or the Auth public certificate. The robot does not need Gate, TargetItem, or Manager secret keys. Session keys are obtained from Auth at runtime, not copied.

## 3. Connect through the Mac

Use the Mac as a relay so the server does not need direct Tailscale access. Replace the old Mac-to-server tunnel with this combined tunnel; do not bind the same local ports twice. Stop the server's old Forklift Agent first so server port 10003 is free.

Mac terminal A:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:21900:127.0.0.1:21900 \
  -L 127.0.0.1:11435:127.0.0.1:11435 \
  -R 127.0.0.1:10003:100.101.153.11:10003 \
  -R 127.0.0.1:21100:127.0.0.1:21100 \
  -R 127.0.0.1:21101:127.0.0.1:21101 \
  -R 127.0.0.1:21200:127.0.0.1:21200 \
  sunyoung@10.218.101.248
```

Mac terminal B:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -R 127.0.0.1:21900:127.0.0.1:21900 \
  -R 127.0.0.1:11435:127.0.0.1:11435 \
  -R 127.0.0.1:21100:127.0.0.1:21100 \
  -R 127.0.0.1:21101:127.0.0.1:21101 \
  -R 127.0.0.1:21200:127.0.0.1:21200 \
  ROBOT_USER@100.101.153.11
```

The robot's local ports reach the Mac resources and bridge. Robot ports 21900 and 11435 reach the server through the Mac's local forwards. The server's port 10003 reaches the robot's A2A service through the Mac. These are separate bidirectional connections; one SSH direction alone does not expose every service.

## 4. Configure addresses

On the robot, set `authInfo.host` to `127.0.0.1` in `forkliftAgent.config`. Set Gate and TargetItem hosts in `targetServerInfoList` to `127.0.0.1`, keeping ports 21100 and 21101.

Robot `a2a-multi-agent/.env`:

```dotenv
FORKLIFT_URL=http://127.0.0.1:10003/
TOOL_LLM_URL=http://127.0.0.1:11435/v1
API_KEY=ollama
SUBAGENT_MODEL=llama3.2:3b
IOTAUTH_FORKLIFT_CONFIG=configs/net1/forkliftAgent.config
IOTAUTH_NODE=node
IOTAUTH_TIMEOUT_SECONDS=30
IOTAUTH_ITEM_GROUP=TargetItem
IOTAUTH_GATE_GROUP=Gate
GAZEBO_DELIVERY_URL=http://127.0.0.1:21200
LATENCY_ENABLED=true
```

`FORKLIFT_URL` advertises the address reachable by Manager through the server-side tunnel. It is intentionally the same logical URL as before, even though the process runs on the robot.

On the server:

- Remove `agents.forklift_agent` from `LOCAL_AGENT_MODULES`; keep Manager and any other locally running workers.
- Use `http://127.0.0.1:10003/` for the Forklift address in `IOTAUTH_AGENT_GROUPS`, mapped to the provisioned group `ForkliftAgent`. Keep the other deployment mappings.
- Update any explicit `AGENT_URLS` override to match that address, or remove the override.
- In the graph used by Manager, Gate and TargetItem endpoints must be `127.0.0.1:21100` and `127.0.0.1:21101`. Manager passes these endpoints to the robot, overriding the worker's fallback list.

On the Mac, Gate and TargetItem must listen on local ports 21100/21101 reachable via `127.0.0.1`. Use that host in their `listeningServerInfo` settings. Keep Gazebo and the bridge running as before. Resource Auth connections can use Mac port 21900.

## 5. Run on the robot's monitor

Open a local terminal on the robot, activate its Python environment, and run:

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
python -m agents.forklift_agent --host 0.0.0.0
```

The bind address allows the Mac's Tailscale connection to reach port 10003. The advertised card URL remains the tunneled URL configured above.

The terminal displays milestones such as:

```text
STATUS | Forklift Agent | Delivery task started
STATUS | Forklift Agent | Session key received: TargetItem
STATUS | Forklift Agent | Secure connection established: TargetItem
STATUS | Forklift Agent | RETRIEVE_ITEM acknowledged by TargetItem
STATUS | Forklift Agent | Session key received: Gate
STATUS | Forklift Agent | Secure connection established: Gate
STATUS | Forklift Agent | OPEN_GATE acknowledged by Gate
STATUS | Forklift Agent | Gate opening confirmed in Gazebo
STATUS | Forklift Agent | Item placed in the secure zone (Gazebo)
```

Key/connection milestones are observed from a metadata-only timing file while the command runs. `OPEN_GATE acknowledged` confirms command publication. The final two messages are printed after the bridge's `finish` action confirms the complete delivery sequence, which includes checking the gate angle and placing the box. They report simulated actions, not ROSMASTER physical motion. Failures produce a failure message instead of successful placement.

On the server, verify the routed card before sending a task:

```bash
curl --fail http://127.0.0.1:10003/.well-known/agent-card.json
```

On the robot, verify the bridge route:

```bash
curl --fail http://127.0.0.1:21200/status
```

Restart Manager after changing its environment and send the existing delivery request. Worker latency files are saved on the robot and included in the Manager result. Since Manager and worker now use different hosts, `a2a_task_delivery` requires verified clock synchronization before treating wall-clock differences as latency; see the main README.
