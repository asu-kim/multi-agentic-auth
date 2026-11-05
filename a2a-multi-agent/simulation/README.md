# SST Warehouse Delivery Demo

## 1. Install Gazebo on the Mac

Install Homebrew and Command Line Tools compatible with your macOS version. Then run these commands in a Mac terminal. [Gazebo installation guide](https://gazebosim.org/docs/harmonic/install_osx/)

```bash
brew tap osrf/simulation
brew trust osrf/simulation
ulimit -Sn 10240
brew install gz-harmonic
gz sim --versions
```

The reported Gazebo Sim version should be `8.x`.

## 2. Deployment

| Computer | Components |
|---|---|
| Ubuntu server | Auth101, LLM service, Manager Agent, Forklift Agent |
| Mac | Gazebo simulation and GUI, `warehouse_bridge.py`, `gate.js`, `targetItem.js` |

The forklift approaches the box, requests `RETRIEVE_ITEM` over SST, carries the box to the gate, and requests `OPEN_GATE`. After confirming that the gate is open, it places the box in the marked secure zone. Movement uses Gazebo model poses. SST handles the resource requests and permissions.

Keep the repository code synchronized on both computers. The commands below assume the repository is at `~/multi-agentic-auth` on each computer. Use your existing Python environment and installed Node dependencies. The Mac resource credentials must match the server's Auth database.

## 3. Configure the Server

```
cd ~/multi-agentic-auth/a2a-multi-agent/
cp .env.example .env
```

In `a2a-multi-agent/.env`, check the following values. 

```dotenv
MANAGER_URL=http://127.0.0.1:10000/
FORKLIFT_URL=http://127.0.0.1:10003/
LOCAL_AGENT_MODULES='["agents.manager_agent","agents.forklift_agent"]'
IOTAUTH_AGENT_GROUPS='{"http://127.0.0.1:10000/":"ManagerAgent","http://127.0.0.1:10003/":"ForkliftAgent"}'
IOTAUTH_ENTITY_DIR=/home/USER/multi-agentic-auth/iotauth/entity/node/example_entities
IOTAUTH_MANAGER_CONFIG=configs/net1/managerAgent.config
IOTAUTH_FORKLIFT_CONFIG=configs/net1/forkliftAgent.config
IOTAUTH_GRAPH_FILE=../iotauth/examples/configs/my_agents.graph
IOTAUTH_MANAGER_ENTITY=net1.managerAgent
IOTAUTH_ITEM_GROUP=TargetItem
IOTAUTH_GATE_GROUP=Gate
GAZEBO_DELIVERY_URL=http://127.0.0.1:21200
A2A_TIMEOUT_SECONDS=900
```

Manager discovers the worker addresses in `IOTAUTH_AGENT_GROUPS`, excluding `MANAGER_URL`. Add Drone and Rover mappings and local modules to include them in selection. Remove an old `AGENT_URLS` override if you want to use every mapped worker.

Adjust `IOTAUTH_ENTITY_DIR` if your server repository is elsewhere. In the server graph and worker resource settings, use `localhost:21100` for Gate and `localhost:21101` for TargetItem. Manager needs delegation and revocation privileges for both groups. Configure the Mac resources to reach Auth at `localhost:21900`.

## 4. Open the SSH Tunnel from the Mac

Replace any existing tunnel using these ports. Keep this terminal open.

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:21900:127.0.0.1:21900 \
  -R 127.0.0.1:21100:127.0.0.1:21100 \
  -R 127.0.0.1:21101:127.0.0.1:21101 \
  -R 127.0.0.1:21200:127.0.0.1:21200 \
  user@SERVER_IP
```

Mac port `21900` reaches server Auth101. Server ports `21100`, `21101`, and `21200` reach Mac Gate, TargetItem, and the simulation bridge. This makes `GAZEBO_DELIVERY_URL=http://127.0.0.1:21200` valid on the server.

## 5. Start the Services

Run Auth101 on the Ubuntu server and keep the LLM service running.

```bash
cd ~/multi-agentic-auth/iotauth/auth/auth-server
java -jar target/auth-server-jar-with-dependencies.jar \
  -p ../properties/exampleAuth101.properties
```

On the Mac, run each block below in a separate terminal.

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
gz sim -s -r simulation/warehouse.sdf
```

```bash
gz sim -g
```

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
python3 simulation/warehouse_bridge.py
```

```bash
cd ~/multi-agentic-auth/iotauth/entity/node/example_entities
node gate.js configs/net1/gate.config
```

```bash
cd ~/multi-agentic-auth/iotauth/entity/node/example_entities
node targetItem.js configs/net1/targetItem.config
```

On the Ubuntu server, activate your Python environment and start Manager and Forklift.

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
set -a
source .env
set +a
python -m agents.run_all
```

## 6. Run the Experiment

In a separate server terminal, activate the same Python environment and check connectivity.

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
curl --fail http://127.0.0.1:10000/.well-known/agent-card.json
curl --fail http://127.0.0.1:10003/.well-known/agent-card.json
curl --fail http://127.0.0.1:21200/status
```

The first two requests should return agent cards. The bridge should report `idle`. Send the task to Manager.

```bash
python -m agents.client "Retrieve the box and deliver it to the secure zone through the gate." \
  --capability reach_middle_shelf --capability carry_heavy_items \
  --resource TargetItem --resource Gate --validity '5*min' --json
```

Watch the delivery in the Mac GUI. Manager delegates access before dispatch and revokes it after the worker reports completion. The bridge reports `completed` after placement. The worker returns `simulated` because this is a Gazebo demonstration.

Before another trial, reset the scene from either computer while the tunnel is open.

```bash
curl --fail -X POST http://127.0.0.1:21200/action \
  -H 'Content-Type: application/json' -d '{"action":"reset"}'
```
