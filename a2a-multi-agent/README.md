# A2A card-based agent selection

An autonomous LLM manager reads independent agents' **A2A Agent Cards**, compares
skills and declared limits with the user's request, and selects **one** suitable
agent. There is no proposal/confirmation negotiation, task reservation, before authority delegation. After successful delegation, the manager dispatches a worker task. A `found` result means the card appears suitable;
it does not confirm current availability or actual ability to execute the task.

| Agent | Model | Port | Advertised skill IDs |
| --- | --- | --- | --- |
| Manager | `gpt-oss:20b` | 10000 | `agent_discovery` |
| Drone | `llama3.2:3b` | 10001 | `reach_top_shelf`, `carry_light_items` |
| Rover | `llama3.2:3b` | 10002 | `reach_bottom_shelf`, `carry_light_items` |
| Forklift | `llama3.2:3b` | 10003 | `reach_middle_shelf`, `carry_heavy_items` |

Each agent is a separate server process with its own stored JSON card. The manager
knows configured addresses only; it learns abilities by fetching cards over HTTP
using the A2A SDK's card resolver. User requests to the manager still use A2A 0.3
JSON-RPC `message/send` through `ClientFactory`. After successful IoTAuth delegation, the manager sends a task message to the selected worker. All four agents have their own Ollama client. Worker inference runs only for explicit task requests; card discovery never calls a worker model.

## Decision loop

```text
User -> A2A -> Manager LLM
                 |
                 +-> discover_agent: fetch an A2A card
                 +-> set_requirements: interpret all required skills
                 +-> assess_agent: record the manager's own card-based judgment
                 +-> delegate_authority: grant the selected agent's group authority through IoTAuth
                 +-> dispatch_task: send an A2A task after successful delegation
                 +-> finish_search: select an agent or report no suitable card
                 +-> ask_user: request missing details
```

The LLM chooses actions and discovery order; this is not a fixed workflow. It can
inspect Forklift first and finish early, compare several cards, or retry a failed
lookup. `assess_agent` only stores the manager's decision and reason; it does not
contact another agent or model. The manager must consider descriptions and limits,
not just skill names. For example, Forklift advertises a maximum payload of 2 kg and
flat indoor floors, so the manager should reject a 20 kg pickup request.

The runtime enforces these conditions:

- A positive assessment requires a discovered card covering every required skill.
- `found` requires a positive assessment of that card.
- Explicit user skill IDs cannot change. Changing inferred requirements clears
  previous assessments; refreshing a card clears that card's assessment.
- `not_found` requires inspecting the entire roster and a negative assessment for
  every card covering the required skills. If any cards remain unavailable, the
  result is `error` with an incomplete-search explanation.
- The default budget is 16 model turns and 600 seconds; exceeding either produces
  `error` and preserves completed history.

Free-text limits are interpreted by the manager LLM, not independently verified
by code or hardware. The search selects one agent for the entire request; it does
not divide work across a team.

## Code layout

```text
agents/
├── manager_agent/
│   ├── manager_agent.py           # Autonomous tool loop and manager instructions
│   ├── manager_tools.py           # Card discovery, assessments and result checks
│   ├── manager_agent_executor.py  # User A2A request adapter
│   ├── manager_agent_card.json
│   └── __main__.py
├── drone_agent/                # Class, JSON card and server entry point
├── rover_agent/               # Class, JSON card and server entry point
├── forklift_agent/                   # Class, JSON card and server entry point
├── common/
│   ├── task_executor.py           # Shared explicit worker task handler
│   ├── search_models.py           # Search request/result schemas
│   ├── agent_card_loader.py
│   ├── a2a_client.py
│   ├── ollama_client.py
│   ├── server_runtime.py
│   └── settings.py
├── client.py
├── check_ollama_running.py
└── run_all.py
```

Edit each adjacent `*_agent_card.json` to change its skills or limits, then restart
that agent. Configured URLs override the JSON URL. The A2A `skills` field contains
task abilities; `capabilities` describes transport features such as streaming.
Worker JSON-RPC endpoints accept separate `agent-task/v1` execution requests. No negotiation is performed.

## Run

Use Python 3.12+ and your existing virtual environment:

```bash
cd ~/multi-agentic-auth/a2a-multi-agent
source ~/venv/bin/activate
python -m pip install -e .
# Initial setup only; preserve any existing .env edits.
cp .env.example .env
```

The default Ollama endpoint is `http://127.0.0.1:11435/v1`. Both manager and worker models are required for selection and explicit tasks:

```bash
export OLLAMA_HOST=127.0.0.1:11435
ollama list
ollama pull gpt-oss:20b
ollama pull llama3.2:3b
python -m agents.check_ollama_running
python -m agents.run_all
```

Keep Ollama running. With the sample `LOCAL_AGENT_MODULES`, wait for all four servers to start. Ctrl+C stops the team;
if a child exits, the launcher stops the others. Alternatively start each agent
in a separate terminal with `python -m agents.manager_agent`,
`python -m agents.drone_agent`, `python -m agents.rover_agent`, and
`python -m agents.forklift_agent`.

In another terminal with the same environment:

```bash
python -m agents.client "Find an agent to pick up a box and move it indoors." --json
python -m agents.client "Retrieve a light item from the top shelf." --capability reach_top_shelf --json
python -m agents.client "Retrieve a light item from the bottom shelf." --capability reach_bottom_shelf --json
python -m agents.client "Pick up a 20 kg box." --capability reach_middle_shelf --json
python -m agents.client "Find an agent that can swim." --capability swimming --json
python -m agents.client "Reach the top shelf and pick up boxes." --capability reach_top_shelf --capability reach_middle_shelf --json
```

Expected selections: Forklift, Drone, Rover; the last three requests should
produce `not_found`. Natural-language requirements and free-text feasibility
judgments depend on the model. Explicit `--capability` values fix skill IDs but
do not replace the manager's assessment of constraints.

## Results and diagnostics

Results contain `status` (`found`, `not_found`, `input_required`, or `error`),
`selected_agent` (name, URL, advertised capabilities and manager's `reason`),
`assessments`, `discovered_agents`, `attempts`, `manager_actions`, and
`manager_turns`. There is no `offer_id`. `attempts` records discovery errors and
manager assessments, not worker replies. If the manager asks a question, send a
new request containing the original task and the answer; searches are independent.

`--trace` displays executed tool actions. `--thoughts` displays reasoning text
returned by Ollama after the search completes. Provider `reasoning`,
`reasoning_content`, and `thinking` string fields are preserved verbatim without
an extra inference call. Missing reasoning is reported explicitly; ordinary
answer text is not substituted. `--json` stays valid JSON with either flag.
Streaming is disabled. Historical `out.json`, if present, is an old run artifact;
run the client again to generate output in the current format.

Client exit codes are 0 for `found`, 1 for other structured results, and 2 for
handled HTTP/configuration failures. Discovery failure is not evidence that an
agent lacks a skill. A successful selection can still finish early even when a
different agent was unreachable.

Configure `MANAGER_MODEL`, `TOOL_LLM_URL`, `MANAGER_MAX_TURNS`,
`MANAGER_TIMEOUT_SECONDS`, `LLM_TIMEOUT_SECONDS`, `A2A_TIMEOUT_SECONDS`, and the
four `*_URL` values in `.env`. Optional comma-separated `AGENT_URLS` overrides the
worker roster. Keep the A2A timeout above the manager deadline. Exported variables
override `.env`; restart affected processes after changes. `SUBAGENT_MODEL` sets the shared worker model (default `llama3.2:3b`).
`FORKLIFT_AVAILABLE` is no longer used. Availability is not checked during selection.

```bash
curl http://127.0.0.1:10003/.well-known/agent-card.json
curl http://127.0.0.1:11435/v1/models
python -m unittest discover -s tests -v
```

Tests use real A2A SDK discovery and manager JSON-RPC messaging through ASGI
transports, with deterministic model doubles. They cover card-only worker traffic,
selection guards, constraint rejection, retries, independent concurrent searches,
changed requirements/cards, deadlines and model failures. They do not validate a
live model's judgment. Local clients ignore proxy environment variables. The A2A endpoints have no authentication and are intended for local development.
Agent authority delegation uses the configured IoTAuth manager identity.


## Explicit worker tasks

The manager dispatches work after successful delegation. Cards advertising `secure_communication` receive
a secure-connection task; other workers, including the current Forklift card, receive the original request. To ask a worker to perform a task, use the separate client:

```bash
python -m agents.task_client "Plan retrieving a light item from the top shelf." --agent drone --json
python -m agents.task_client "Plan retrieving a light item from the bottom shelf." --agent rover --json
python -m agents.task_client "Plan moving a 1 kg box to station B on a flat indoor floor." --agent forklift --json
```

You can also pass `--url` with the selected agent's URL instead of `--agent`.
The A2A `DataPart` request is `{"protocol":"agent-task/v1", "action":"execute",
"query":"..."}`. Plain-text A2A messages are also accepted. This is a one-shot
request/response interface; it does not preserve conversation state or negotiate.

Workers share `TaskAgent` and `TaskExecutor`, with their own model clients, cards,
and role instructions. Drone models top-shelf reach and light-item transport; Rover models bottom-shelf reach and light-item transport. Both return simulated plans, with no hardware interface. Capabilities do not confer access permission.
Forklift produces simulated plans for physical tasks and sends no hardware commands.
Its secure-communication tool can return `completed` after a confirmed IoTAuth connection. Actual forklift
execution requires a future hardware/tool adapter.

Results contain `agent_name`, `status`, and `output`. Status is `completed`,
`simulated`, `input_required`, `declined`, or `error`. The task client exits with
0 for completed/simulated results, 1 for other structured results, and 2 for
handled HTTP/configuration failures. Missing inputs and unsupported tasks are
handled by the worker LLM using its declared card limits. Model errors are returned
as structured errors. The default worker model can be changed with
`SUBAGENT_MODEL` in `.env`; restart workers after changing it. Constructing an
agent without a model is supported for card-only tests, but task requests then
return an explicit configuration error.

## Manager authority delegation

After identifying and positively assessing an agent with the desired capabilities,
the manager calls `delegate_authority` with that agent's `agent_url`, before
`finish_search`. The tool resolves the selected URL through `IOTAUTH_AGENT_GROUPS`
and sends `delegateAuthority <configured-group> <resource> <validity>` using the explicit user scope or a catalog-selected target and configured validity. It does not
infer authorization identities from display names or untrusted card text.

Set `IOTAUTH_AGENT_GROUPS` to a JSON object mapping each selectable A2A URL to its
actual registered IoTAuth group. For example (the names must match your generated SST graph):

```dotenv
IOTAUTH_AGENT_GROUPS='{"http://127.0.0.1:10000/":"ManagerAgents","http://127.0.0.1:10001/":"DroneAgents","http://127.0.0.1:10002/":"RoverAgents","http://127.0.0.1:10003/":"ForkliftAgents"}'
```

The mapping includes Manager, Drone, Rover, and Forklift. Manager remains
outside the default selectable worker roster. Keep these URLs aligned with the
deployment and ensure Auth grants the manager delegation privileges for each subject. There is no hard-coded fallback subject;
a missing mapping reports an error without sending a command. URL matching ignores
a trailing slash. This grants authority to the selected agent's configured **group**,
which may contain more than one entity.

Provision the manager's IoTAuth config, keys, and Auth registration first. This
checkout does not include `configs/net1/managerAgent.config`; the integration
reports a configuration error until it is supplied. Set `IOTAUTH_MANAGER_CONFIG`
if its path differs (the directory is `configs`, not `config`). Install the Node
entity dependencies and run the corresponding IoTAuth Auth service as usual.

The tool launches `node user.js configs/net1/managerAgent.config --privilege-json`
from the sibling `iotauth/entity/node/example_entities` directory. It waits for
initialization to finish before writing the command to stdin. The optional flag
adds JSON events while preserving normal interactive logs. Success requires both
`received privilege response!` and `Finished privilege request`, plus a structured
`DelegationGrant` policy with `IsDelegated: 1` and matching `RequestingGroup`, `TargetType: Group`, and `Target`. The returned policy is preserved in `manager_actions`. A mismatched grant is reported as uncertain/error and is not retried.

`IOTAUTH_ENTITY_DIR`, `IOTAUTH_MANAGER_CONFIG`, `IOTAUTH_NODE`, and
`IOTAUTH_TIMEOUT_SECONDS` configure the bridge. A timeout or failed response is
reported as an error; a sent request may already have changed Auth state, so it
is not retried automatically. Repeated tool calls within the same search reuse
the first result. The subprocess is cleaned up on success, failure, or cancellation.
This does not send a task to the Forklift or verify its physical availability.

## Forklift secure communication tool

The forklift can select `establish_secure_communication` through its structured LLM
action response. Send an explicit worker task:

```bash
python -m agents.task_client "Establish secure communication with the configured resource." --agent forklift --json
```

The tool launches `node user.js configs/net1/forkliftAgent.config`,
waits for the complete `current parameters: {...}` initialization log, then sends
`initComm net1.resource`. Success requires the exact log `switching to IN_COMM`. The forklift server
keeps the Node process alive, reuses a live connection, and closes it on shutdown.
Failures and timeouts return task status `error`; confirmed connection returns
`completed`. Physical tasks still return simulated plans. After successful delegation the manager forwards the original task unless the selected card advertises `secure_communication`.

Configure `IOTAUTH_FORKLIFT_CONFIG` (use `config/net1/forkliftAgent.config` if that is your
actual path), `IOTAUTH_FORKLIFT_RESOURCE`, and the shared `IOTAUTH_ENTITY_DIR`,
`IOTAUTH_NODE`, and `IOTAUTH_TIMEOUT_SECONDS`. Provision the forklift credentials and
Auth policy and start the resource server first. Its name must appear in the forklift
config's `targetServerInfoList`. This tool connects only; it sends no application
payload or physical forklift commands.

## Manager-to-worker handoff

The manager now follows assessment → authority delegation → `dispatch_task` →
`revoke_authority` (after completed/simulated) → `finish_search`. `dispatch_task` uses the selected discovered card and A2A
`agent-task/v1`, and requires a current positive assessment plus confirmed delegation.
For a forklift advertising `secure_communication`, it asks the forklift to establish a
connection to its configured `IOTAUTH_FORKLIFT_RESOURCE`; it does not start physical
pickup or movement. Other selected workers receive the original request.

The worker result appears in `task_result`, the action trace, and the final summary.
Errors/declines become overall `error`, and requests for clarification become
`input_required`. Duplicate dispatch calls within a search reuse the recorded result;
uncertain failures are not automatically retried. The forklift service must be running
with its Node config and resource target configured. Merely finding an agent does
not prove that its task succeeded; inspect the worker result.

### Revoke delegated access after completion

The manager's `revoke_authority(agent_url)` tool uses the subject and object saved
from the confirmed `delegateAuthority` response. It runs the existing `user.js`
with the manager's configuration and sends `revoke <delegatee> <delegated_object>`
(for example, `revoke ForkliftAgents Doors`). The model cannot substitute a different
delegatee or resource. The manager must have the matching DelegationRevoke privilege.

A worker result of `completed` or `simulated` enables this tool; `finish_search`
requires a revocation attempt before reporting that result. Auth must return a
matching structured DelegationRevoke response for revocation to count as successful.
The final response includes `revocation_result`. Failed or uncertain revocations
produce an overall error stating that access may remain active. Repeated tool calls
reuse the recorded result instead of sending another request.

Completion means the task the worker actually reported, including a connection-only
task; it does not prove physical work occurred. Pending, failed, or uncertain worker
execution does not trigger this completion tool. Cleanup is managed within the active
manager request, not a persistent recovery service: a manager crash or exhausted tool
budget can leave a grant active. IoTAuth revokes the group/resource policy (including
its downstream delegations); this does not itself close existing secure sessions or
erase previously issued session keys. Avoid sharing the same grant across concurrent
tasks that still need access.

### Explicit forklift tool dispatch

Manager connection requests include `requested_tool: "establish_secure_communication"`
in the `agent-task/v1` payload. Forklift executes this allowlisted, zero-argument tool
directly, without asking its LLM to reinterpret the request. The tool reads
`IOTAUTH_FORKLIFT_CONFIG` and `IOTAUTH_FORKLIFT_RESOURCE`; credentials stay in the Node
configuration. Missing files, denied authorization, and connection failures are
reported by the tool, not guessed by the model. Ordinary natural-language forklift
requests still use LLM action selection. Other workers reject explicit tools they
do not implement. Restart both manager and forklift after updating the code.

## Camera-guided box pickup (ROSMASTER M3 Pro / ROS2)

`grasp_box` is a zero-argument forklift tool. It uses a synchronized RGB/depth stream
and calibrated depth contours to select a single rectangular foreground box in a
configured pickup region. It does not filter colors or require mouse/keyboard
selection. This is a constrained tabletop detector, not general object recognition:
a rectangle can be a non-box object; provide a clear scene with one suitable box.

The implementation adapts the supplied `shape_recognize.py` perception approach
and `grasp_desktop.py` camera transform, FK/IK service fields, wrist convention,
and arm message fields. It does not import/run the interactive vendor demo.
`KCF_follow_desktop.py` requires an external `/pos_xyz` producer and is not used.
The worker moves to the viewing pose, detects three stable frames, checks depth,
size, target workspace and joint bounds, approaches, closes, then lifts and holds.
No chassis commands, sorting release, or automatic repeated grasp are issued.

### Deploy and calibrate

1. Copy `config/camera_grasp.example.json` to the forklift. The included camera/arm
   constants come from the pasted demos and are **starting values**, not verified
   calibration for your forklift. Verify intrinsics, camera-to-end transform, depth
   registration, viewing pose, gripper angles, allowed joint/workspace bounds and
   offsets against `arm_kin/param/offset_value.yaml`. Verify clearance along the
   whole approach/lift path; bounds alone are not collision checking.
2. Set `depth_scale` from actual depth units: the supplied demo divides float depth
   by 1000, so its example is .001. If your camera publishes meters, use 1. Do not
   infer units only from 16UC1 versus 32FC1. RGB and depth must be registered and
   match the calibrated dimensions; no blind resizing is performed.
3. Set `calibrated` and `depth_registered_to_color` true only after checking them.
   Set `attempt_lock` to a persistent writable path on the forklift. Set the ROI and
   object size/depth ranges for your tabletop. Clock stamps must match ROS time.
4. Source ROS2 and `~/yahboomcar_ws/install/setup.bash`, then start the camera,
   arm controller and kinematics service, using the usual
   `ros2 launch M3Pro_demo camera_arm_kin.launch.py`. Do **not** also run grasp,
   grasp_desktop, shape_recognize, KCF or other arm/chassis command producers.
5. The separate ROS interpreter needs rclpy, arm_msgs, arm_interface, sensor_msgs,
   cv_bridge, message_filters, NumPy and OpenCV. The agent's Python 3.12+ venv
   does not need to import ROS. Both processes inherit the same ROS environment.

Preview perception with the arm already at the configured viewing pose:

```bash
/usr/bin/python3 agents/forklift_agent/ros_camera_grasp.py --config /home/jetson/camera_grasp.json --preview
```

Preview publishes no motion. It assumes the configured arm pose; this interface
has no measured-joint feedback. A live grasp also assumes the commanded viewing
pose is reached after its motion time. Test/calibrate this before use.

Configure forkliftAgent:

```dotenv
FORKLIFT_GRASP_ENABLED=true
FORKLIFT_GRASP_CONFIG=/home/jetson/camera_grasp.json
FORKLIFT_ROS_PYTHON=/usr/bin/python3
```

Then request the actual tool explicitly:

```bash
python -m agents.task_client "Use the camera to grab the box." --agent forklift --tool grasp_box --json
```

Natural-language worker requests can select the same tool. The manager's existing
automatic handoff follows the selected card capabilities; it does not itself start
physical pickup. Send the explicit worker request after that handoff.

### Results and stopping

`completed` means approach/close/lift commands were sent and their configured
motion times elapsed. There is no grip/contact feedback in the supplied interface;
the response explicitly says pickup is not sensor-verified. No `grasp_done` from
another node is mistaken for completion of this request.

The tool permits one attempt per agent process and creates the persistent
`attempt_lock` before moving. After success, timeout or interruption, inspect and
reset the forklift before manually removing that file and restarting the agent.
This prevents automatic reopening of a potentially loaded gripper. Timeout or
cancellation terminates the child and stops subsequent commands. It cannot retract
already-issued servo motion: the supplied API has no verified emergency-stop
command. Keep the physical stop accessible during supervised commissioning.

Local tests cover synthetic images and mocked ROS messages/services. Real camera,
calibration, contact sensing, collision avoidance and physical pickup are not
validated on the development Mac.


## Warehouse agent names (paper Section 2.3)

`language_agent` has been renamed to `drone_agent` (`DroneAgent`, port 10001), and `analytics_agent` to `rover_agent` (`RoverAgent`, port 10002). Use `DRONE_URL` and `ROVER_URL` instead of the old URL variables, and update local `IOTAUTH_AGENT_GROUPS` mappings to `DroneAgents` and `RoverAgents`. Existing local environment files and SST graphs are not rewritten automatically; regenerate the corresponding graph and authorization configuration with matching groups.

The cards advertise exactly the two modeled capabilities for each agent: drone = reach top shelf + carry light items; rover = reach bottom shelf + carry light items. Neither advertises middle-shelf reach or heavy-item transport. No numerical payload limit is assumed. The Forklift Agent advertises middle-shelf reach and heavy-item transport. These are modeled capabilities; the existing tabletop grasp adapter is not a physical forklift implementation. Protected retrieval still requires valid access permission.


The former `robot_agent` package is now `forklift_agent`; launch it with `python -m agents.forklift_agent` or select `--agent forklift`. Use `FORKLIFT_URL` and `IOTAUTH_FORKLIFT_CONFIG` / `IOTAUTH_FORKLIFT_RESOURCE`, and map its endpoint to `ForkliftAgents`. Regenerate SST configuration for `net1.forkliftAgent` before connecting (default `configs/net1/forkliftAgent.config`). Existing SST files are not rewritten by this rename. Keep the configured persistent grasp-attempt lock path unchanged when migrating an existing hardware deployment so the interlock remains effective.


## Configure external agents and local startup

`AGENT_URLS` is the discovery roster: these endpoints must be running independently. An endpoint does not tell the launcher how to start a remote process. `LOCAL_AGENT_MODULES` is a JSON list of trusted local Python modules to start with the current interpreter. The default when omitted is only `agents.manager_agent`; the example environment explicitly starts all four bundled agents.

```dotenv
AGENT_URLS=http://drone-host:10001/,http://forklift-host:10003/
LOCAL_AGENT_MODULES='["agents.manager_agent"]'
IOTAUTH_AGENT_GROUPS='{"http://drone-host:10001/":"DroneAgents","http://forklift-host:10003/":"ForkliftAgents"}'
```

For custom local agents, list their installed entry-point modules, e.g. `["my_agent.server","agents.manager_agent"]`. Modules own their server configuration; the launcher does not infer modules or bind addresses from endpoints. Built-in workers continue using `DRONE_URL`, `ROVER_URL`, and `FORKLIFT_URL`. Run `python -m agents.run_all`; only listed local processes are supervised and stopped together. External services are never started or terminated by the launcher.

## Request-specific delegation

Supply the exact resource **group** registered in IoTAuth and the requested duration:

```sh
python -m agents.client "Retrieve the heavy item from the middle shelf." \
  --capability reach_middle_shelf --capability carry_heavy_items \
  --resource Doors --validity '30*min' --json
```

A2A callers can provide `"delegation": {"resource": "Doors", "validity": "30*min"}` alongside `query` and `required_capabilities`. Either field or the entire object may be omitted. Explicit values are preserved; omitted values are resolved from the graph as described below.

Resource names must be single tokens containing letters, digits, underscores, periods, or hyphens. Durations are positive integers followed by `*sec`, `*min`, `*hour`, or `*day`. Invalid values are rejected before sending a command. Auth must already have the manager's parent access policy and matching delegation privilege for the requested recipient and resource. The duration is a request: Auth may shorten the grant to its privilege or parent-policy expiration. Actual expiration is in the returned policy; session-key absolute/relative validity still comes from Auth policy configuration.

Dynamic delegation does not change a worker's configured resource endpoint or implement hardware access checks. Configure worker adapters for the matching protected resource. After a completed or simulated worker result, the manager calls `revoke_authority` to revoke the confirmed grant. Only the Python framework is modified; no IoTAuth source changes are needed.


## Manager resource selection from the GUI graph

Set these values in `.env` (relative paths resolve from `a2a-multi-agent`, not the shell's current directory):

```dotenv
IOTAUTH_GRAPH_FILE=../iotauth/examples/configs/my_agents.graph
IOTAUTH_MANAGER_ENTITY=net1.managerAgent
```

Use the graph saved and provisioned through the GUI. Resource entities are the graph entities with `host` and `port`; their groups are the delegation targets. Optional descriptions entered in the GUI help distinguish resources. The manager entity identifies which group's grant privileges and parent access policies to use. No agent's card can change this identity.

For example, a user can now send:

```sh
python -m agents.client "Retrieve the heavy item through the warehouse entrance." \
  --capability carry_heavy_items --json
```

The manager calls `list_resources`, reasons about the task using names/descriptions and allowed recipients, then calls `select_resource` with an exact catalog group and an explanation. It asks for clarification if there is no clear match, no usable catalog, or more than one resource group is needed. Model selection is not authorization: the runtime validates the group, recipient, parent policy, and grant privilege, and Auth remains authoritative.

The runtime uses explicit `--validity` when supplied. Otherwise it uses the matching graph grant's configured validity (the shortest if duplicate grants exist). Requests exceeding that configured limit are rejected rather than silently changing the user's choice. Actual expiration may be shorter under Auth's current policy. Configure appropriately short validity in the graph; the model does not estimate task duration.

`resolved_delegation` and `resource_selection_reason` in JSON results show the selected scope and explanation. Selection is locked after a delegation attempt, including uncertain failures. Catalog contents are snapshotted on first access per task; edits apply to subsequent tasks. An explicit resource/duration remains usable without a catalog, but if a graph is configured, it must be readable and permit that scope.

This supports one resource **group** per task. A group may contain multiple endpoints, so choosing it is not per-item authorization. Worker endpoint routing remains a separate feature; the selected catalog endpoint is not automatically connected by the existing forklift adapter.
