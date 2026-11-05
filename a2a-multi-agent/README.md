# A2A Warehouse Agents

The Manager reads the workers' A2A Agent Cards, compares their capabilities and limits with the task, and selects one subagent. It delegates resource access through SST, sends the task over A2A, and waits for the worker result. After successful execution, it revokes the delegated permissions.

| Agent | Default port | Advertised capabilities |
| --- | --- | --- |
| Manager | 10000 | Task coordination |
| Drone | 10001 | `reach_top_shelf`, `carry_light_items`, `deliver_to_safe_zone` |
| Rover | 10002 | `reach_bottom_shelf`, `carry_light_items`, `deliver_to_safe_zone` |
| Forklift | 10003 | `reach_middle_shelf`, `carry_heavy_items`, `deliver_to_safe_zone` |

All three workers advertise `deliver_to_safe_zone`, which includes authorized gate traversal and placement in the secure zone. Their shelf and payload limits still determine which worker fits a task.

Agent Cards describe modeled capabilities. The current Gazebo scene visualizes delivery with a forklift model. See the [simulation setup](simulation/README.md) for Gazebo installation and the Ubuntu–Mac deployment.

## Execution Flow

```text
User request
  -> Manager discovers Agent Cards and assesses suitability
  -> Manager delegates access to the required resource groups
  -> Manager dispatches the task to the selected subagent over A2A
  -> Subagent executes the shared warehouse delivery action
  -> Subagent reports the result
  -> Manager revokes the confirmed grants and returns the result
```

The Manager LLM chooses which cards to inspect and assesses task suitability. The runtime checks that the selected card covers all required skills and that delegation succeeded before dispatch. A single worker handles each task.

With confirmed grants for both the item and gate, `dispatch_task` sets `requested_tool` to `deliver_to_safe_zone`. The selected worker calls the shared `TaskAgent.deliver_to_safe_zone()` method, which invokes `warehouse_delivery.deliver()` with that worker's SST identity.

Delivery follows this sequence when Gazebo is enabled.

1. Move to the box.
2. Establish an SST connection to TargetItem and send `RETRIEVE_ITEM`.
3. Receive `retrieved`, then carry the box to the gate.
4. Establish an SST connection to Gate and send `OPEN_GATE`.
5. Receive `open` and verify the simulated gate angle.
6. Enter the secure zone, place the box, and report completion.

Each secure command uses a fresh Node client. Credentials stay in the worker's SST configuration. Gazebo movement uses model pose updates. A simulator error or an unexpected resource response produces an error result.

## Code Layout

| File | Responsibility |
| --- | --- |
| `agents/manager_agent/manager_agent.py` | Manager LLM loop and instructions |
| `agents/manager_agent/manager_tools.py` | Discovery, assessment, delegation, dispatch, and revocation |
| `agents/manager_agent/iotauth_delegate.py` | Manager's SST delegation and revocation requests |
| `agents/{forklift,drone,rover}_agent/` | Worker class, Agent Card, and server entry point |
| `agents/common/task_executor.py` | Receive A2A tasks and return worker results |
| `agents/common/task_agent.py` | Shared worker execution and natural-language action selection |
| `agents/common/warehouse_delivery.py` | SST request sequence and simulation coordination |
| `agents/common/resource_command.js` | Send one secure resource command and verify its response |
| `simulation/warehouse_bridge.py` | Move the Gazebo models and check the gate angle |

## Setup

Set up a separate Python environment on each machine that runs agents (the server and robot). The project requires Python 3.12 or later, as declared in `pyproject.toml`. The commands below assume `python3.12` is installed; a newer Python interpreter can be used instead. If it is unavailable, install a compatible interpreter before continuing.

First-time setup on each agent host:

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
test -f .env || cp .env.example .env
```

`python3.12 -m venv .venv` creates the environment. `source .venv/bin/activate` activates it in the current terminal. `pip install -e .` installs the package and all Python dependencies listed in `pyproject.toml`, including A2A SDK, HTTPX, Pydantic, python-dotenv, and Uvicorn. An existing environment with Python 3.12+ can be activated and used instead; do not copy a virtual environment between machines.

In each new terminal used to launch an agent:

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
source .venv/bin/activate
```

The Manager and workers also invoke Node SST clients. With Node.js and npm installed, install their dependencies on each agent host:

```bash
cd ~/multi-agentic-auth/iotauth/entity/node
npm install
```

The Mac's `warehouse_bridge.py` uses the Python standard library and does not require the agent environment. Gate and TargetItem need the Node dependencies above on the Mac. Gazebo installation is described in the simulation guide.

Configure these settings in `.env`.

- `TOOL_LLM_URL`, `MANAGER_MODEL`, and `SUBAGENT_MODEL` select the LLM endpoint and models. The default endpoint is `http://127.0.0.1:11435/v1`.
- `LOCAL_AGENT_MODULES` lists the local agent processes to start. The example starts Manager, Drone, Rover, and Forklift. Remote agents must be started on their own hosts.
- `MANAGER_URL`, `DRONE_URL`, `ROVER_URL`, and `FORKLIFT_URL` set the advertised A2A addresses. Each server listens on `127.0.0.1` by default. For remote access, start its module with an appropriate `--host` address.
- `IOTAUTH_AGENT_GROUPS` supplies the discovery addresses and maps each address to its registered SST group. Manager excludes its own `MANAGER_URL` and reads worker capabilities from Agent Cards. Match group names to the provisioned Auth database.
- `AGENT_URLS` is optional. Set it only to restrict discovery to a subset of the mapped addresses. When no group mapping is configured, discovery falls back to the worker `*_URL` settings.
- `IOTAUTH_GRAPH_FILE` and `IOTAUTH_MANAGER_ENTITY` select the resource catalog and Manager identity.
- `IOTAUTH_MANAGER_CONFIG`, `IOTAUTH_FORKLIFT_CONFIG`, `IOTAUTH_DRONE_CONFIG`, and `IOTAUTH_ROVER_CONFIG` select each entity's configuration.
- `IOTAUTH_ENTITY_DIR` optionally overrides the default sibling `iotauth/entity/node/example_entities` directory.
- `IOTAUTH_ITEM_GROUP` and `IOTAUTH_GATE_GROUP` default to `TargetItem` and `Gate`.
- `GAZEBO_DELIVERY_URL` enables the Mac simulation bridge. With the documented SSH tunnel, use `http://127.0.0.1:21200` on the Ubuntu server.

Provision Auth, entity credentials, and Manager delegation/revocation privileges before running a task. Resource endpoints come from the selected graph or, for direct worker calls without a graph, from the worker configuration. Resource servers must be running. Without `GAZEBO_DELIVERY_URL`, delivery verifies the two SST replies and reports simulated placement.

Start the configured LLM service, Auth, resources, and simulation as described in the [simulation guide](simulation/README.md). Then start the agent team.

```bash
python -m agents.check_ollama_running
python -m agents.run_all
```

Restart agents after editing `.env`. Exported environment variables take precedence over `.env`. The A2A services are intended for local development and do not authenticate callers.

## Send a Task to Manager

Run the client in a separate terminal using the same Python environment.

```bash
python -m agents.client "Retrieve the box and deliver it to the secure zone through the gate." \
  --capability reach_middle_shelf --capability carry_heavy_items --capability deliver_to_safe_zone \
  --resource TargetItem --resource Gate --validity '5*min' --json
```

Explicit capabilities fix the required skill IDs. The Manager still assesses the card's descriptions and limits. Without explicit capabilities, it infers requirements from the task.

Explicit resource groups and duration are preserved. When omitted, the Manager uses the graph catalog or asks for clarification. The catalog limits delegation scope, and Auth determines the granted policy and expiration. Delegation applies to resource groups, which can contain multiple entities.

Results include `selected_agent`, `assessments`, `manager_actions`, `task_result`, and `revocation_result`. Use `--trace` to inspect tool calls. `--thoughts` displays reasoning text returned by the model. The Manager reports `found`, `not_found`, `input_required`, or `error`; inspect the worker and revocation results to confirm execution.

## Send a Task Directly to a Worker

A direct request requires access permissions to be provisioned or delegated in advance. Manage revocation separately.

```bash
python -m agents.task_client "Deliver the box to the secure zone." \
  --agent forklift --tool deliver_to_safe_zone --json
```

Use `--agent drone`, `--agent rover`, or `--url` to target another worker. Explicit tool requests execute the shared delivery action directly. A natural-language request without `--tool` uses the worker LLM to select delivery or produce a simulated plan. In that path, `requires_safe_zone_delivery=true` selects the same delivery action.

Workers return `completed`, `simulated`, `input_required`, `declined`, or `error`. Gazebo delivery returns `simulated`. The final execution result comes from the delivery code. Agent Cards are available at `/.well-known/agent-card.json` and can be fetched independently of task execution.

## Completion and Revocation

Manager dispatch requires a positive assessment and confirmed delegation. Duplicate dispatch calls within one request reuse the recorded result. Uncertain execution is not automatically retried.

After the worker reports `completed` or `simulated`, Manager calls `revoke_authority` using the exact grants recorded earlier. A failed or uncertain revocation produces an error. Revocation removes access policies but leaves existing secure sessions and previously issued keys intact. Test new key requests when evaluating revocation or expiration.

Cleanup runs within the active Manager request. A process crash or interrupted task may leave a grant active. Inspect Auth state before repeating a failed experiment. Reset the Gazebo scene between trials using the command in the simulation guide.



## Latency Measurements

Latency recording is enabled by default. Restart the agents after deploying the updated code. No changes to `SecureCommClient.js` or the SST library are required. The command adapter wraps the existing `iotAuth.sendSessionKeyReq` function inside its fresh Node process to measure the request and response callback. It resolves the same module instance as the accessor.

Run a task as usual:

```bash
python -m agents.client "Retrieve the heavy target item from the middle shelf and deliver it to the secure zone through the gate." \
  --capability reach_middle_shelf --capability carry_heavy_items --capability deliver_to_safe_zone \
  --resource TargetItem --resource Gate --validity '5*min' --json
```

Each request writes a JSON file under `a2a-multi-agent/results/latency/` on the service host. Manager files are named `<trace_id>_manager_<id>.json`; worker files use `_worker_`. The Manager file includes the worker trace under `worker_traces`, even when the worker runs on another host. The A2A result also includes this data in `latency`. Direct worker requests save worker traces only.

Set `LATENCY_OUTPUT_DIR` in `.env` to change the output directory. Relative paths resolve against the service working directory. Set `LATENCY_ENABLED=false` to disable recording. Files contain durations in milliseconds, outcomes, resource names, and timing events. They exclude session keys, task text, and model reasoning.

| Metric | Measurement boundary |
|---|---|
| `agent_selection` | Manager entry to the accepted positive card assessment for the selected agent. Includes LLM decisions and discovery before that assessment. |
| `a2a_card_discovery` | One Agent Card fetch, including HTTP and parsing. |
| `manager_llm`, `worker_llm` | One model call, including generation and response handling. |
| `delegation` | One resource grant call, including Node startup, Auth interaction, confirmation checks, and process cleanup. |
| `revocation` | One resource revocation call, with the same boundary as delegation. |
| `iotauth_node_startup` | Process launch to the `IOTAUTH_READY` signal. |
| `auth_privilege_command_round_trip` | Sending the command to `user.js` through confirmation processing, before process cleanup. This includes local Node handling and Auth interaction. |
| `a2a_task_delivery` | Manager A2A SDK send start to entry into the worker A2A executor, before validation and task execution. Includes SDK/HTTP serialization, transport, decoding, and dispatch queues. Excludes worker LLM, SST commands, and Gazebo execution. |
| `a2a_task_round_trip` | Manager sends a worker task through receiving and validating its final result. Includes worker execution. |
| `worker_execution` | Worker enters task execution through producing its result. |
| `a2a_task_overhead_estimate` | A2A task round trip minus worker trace duration. Includes request/response transport, queues, serialization, and telemetry overhead. An estimate, not one-way network latency. |
| `sst_session_key` | IoTAuth session-key request API invocation through its response callback, including response validation. Excludes Node startup and the resource handshake. |
| `sst_connect` | Requesting a resource connection through the secure-connected callback. Includes key acquisition and handshake. |
| `sst_handshake` | Successful key timing callback through the resource's secure-connected callback, including local setup. |
| `sst_command_reply` | Sending `RETRIEVE_ITEM` or `OPEN_GATE` through its secure reply. A gate reply confirms command publication; Gazebo angle verification occurs in `finish`. |
| `sst_resource_command` | The full resource-command subprocess, including startup and all SST steps. |
| `gazebo_action` | One simulation bridge action, including its animation and HTTP response. |

Use `events` for individual samples and their resource/command attributes. `summary` contains per-metric counts, successful counts, measured sample counts, and total milliseconds. An unverified delivery interval has `duration_ms: null`; its summary has `measured_count: 0` and `total_ms: null`. `total_ms` covers the local Manager or worker request through result generation, before saving its trace. Nested metrics overlap; do not add them to obtain total task time. Failed and cancelled attempts retain timings and status. Steps that were not reached are absent, rather than reported as zero. Missing Node telemetry appears in `warnings`.

Durations use local monotonic clocks (`perf_counter_ns` in Python and `hrtime.bigint` in Node). A shared `trace_id` correlates Manager and worker records. For `a2a_task_delivery`, the sender stamps the A2A message immediately before the SDK send. The worker timestamps entry into its A2A executor and returns the interval with its final result. The Manager copies this event into its own JSON. Although the record is returned after task completion, the interval ends before execution begins.

On the same Linux boot and time namespace, the endpoints use their shared monotonic clock automatically (`clock_basis: shared_monotonic`). This covers Manager and workers running on the same Ubuntu server. In-process tests also share a monotonic clock. Across other clock domains, the JSON records `wall_clock_delta_ms` but leaves `duration_ms` null with `status: clock_sync_unverified`. After verifying clock synchronization between hosts, set `LATENCY_CLOCKS_SYNCHRONIZED=true` on the worker to use the wall-clock delta. Such samples have `status: estimated`; their accuracy depends on the synchronization error. Negative deltas are marked `clock_error`, never clamped to zero. This setting does not synchronize clocks itself.

The send/receive markers measure application-level delivery, not physical packet transit. All other duration metrics remain based on local monotonic clocks. Session-key samples come from fresh Node processes for each resource command; they do not measure session-key cache hits. Measurements include instrumentation overhead. Repeat trials, reset the scene between trials, and report failures separately from successful latency samples. Timing records are excluded from the model's tool observations to avoid enlarging its context. Recording does not trigger retries or additional resource actions.

For a Forklift Agent running on ROSMASTER with Gazebo and resources on a Mac, see [Robot deployment and terminal status](simulation/ROBOT_DEPLOYMENT.md).
