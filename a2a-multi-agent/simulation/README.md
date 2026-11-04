# SST Warehouse Delivery Demo

When the Manager dispatches `deliver_to_safe_zone`, the forklift approaches the box and sends `RETRIEVE_ITEM` over SST. After receiving `retrieved`, it lifts the box and carries it to the gate. Once it receives `open` in response to `OPEN_GATE` and verifies that the hinge angle is at least 1.4 radians, it enters the green SECURE ZONE. It lowers the box onto the yellow pad and reverses away. The worker then reports `simulated` completion, and the existing Manager workflow revokes the delegated permissions.

This demo visualizes motion using Gazebo model poses controlled through `set_pose_vector`. ArduPilot driving, collision avoidance, and physical fork contact require further integration. During transport, the box follows a position in front of the forklift. SST key issuance and request–response exchanges use the existing implementation. The simulation HTTP bridge is a trusted local control channel.

## Mac

Stop the existing `gate_test` simulation because both worlds use the same `/secure_door` topics. Run each command below in a separate terminal.

```bash
cd /Users/sunyoungkim/multi-agentic-auth/a2a-multi-agent
gz sim -s -r simulation/warehouse.sdf
```

```bash
gz sim -g
```

```bash
cd /Users/sunyoungkim/multi-agentic-auth/a2a-multi-agent
python3 simulation/warehouse_bridge.py
```

In the GUI, double-click the `forklift` or `secure_zone` model to center the view, then adjust the zoom. Start Gate and TargetItem in separate terminals as well.

```bash
cd /Users/sunyoungkim/multi-agentic-auth/iotauth/entity/node/example_entities
node gate.js configs/net1/gate.config
```

```bash
cd /Users/sunyoungkim/multi-agentic-auth/iotauth/entity/node/example_entities
node targetItem.js configs/net1/targetItem.config
```

If the default `node` command fails with a simdjson error, use the absolute path to a working Node installation. The Gate process must be able to find `gz` on its `PATH`.

## SSH Tunnel

Stop the existing SSH port-forwarding process and replace it with this tunnel, started on the Mac.

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:21900:127.0.0.1:21900 \
  -R 127.0.0.1:21100:127.0.0.1:21100 \
  -R 127.0.0.1:21101:127.0.0.1:21101 \
  -R 127.0.0.1:21200:127.0.0.1:21200 \
  sunyoung@en4234875l
```

Run Auth101 on Ubuntu. Configure the Mac resources to reach Auth at `localhost:21900`. In the server graph and worker configuration, use `localhost:21100` for Gate and `localhost:21101` for TargetItem. The Mac credentials must match the registrations in the Ubuntu Auth database.

## Server Agents

Synchronize the updated `agents/common/warehouse_delivery.py` and `agents/common/resource_command.js` files to the server repository. Add the following settings to the server’s `a2a-multi-agent/.env`, then restart Manager and Forklift.

```dotenv
IOTAUTH_ITEM_GROUP=TargetItem
IOTAUTH_GATE_GROUP=Gate
GAZEBO_DELIVERY_URL=http://127.0.0.1:21200
A2A_TIMEOUT_SECONDS=900
```

Setting `GAZEBO_DELIVERY_URL` enables visualization for the worker’s `deliver_to_safe_zone` task. If omitted, the existing SST response-verification workflow runs. Manager must use the same item and gate group settings. When using `IOTAUTH_GRAPH_FILE`, check the graph’s group names, endpoints, and Manager delegation and revocation privileges. The Forklift URL in `IOTAUTH_AGENT_GROUPS` must map to the registered `ForkliftAgent` group.

Check connectivity from the server, then start the agents using the existing launcher.

```bash
curl --fail http://127.0.0.1:21200/status
python -m agents.run_all
```

Send a request to Manager from a separate server terminal.

```bash
python -m agents.client "Retrieve the box and deliver it to the secure zone through the gate." \
  --capability reach_middle_shelf --capability carry_heavy_items \
  --resource TargetItem --resource Gate --validity '5*min' --json
```

Manager verifies delegation for both groups before dispatching `deliver_to_safe_zone`. If selection or delegation fails, inspect `manager_actions` and the Auth logs. With permissions already delegated manually, you can test the worker directly.

```bash
python -m agents.task_client "Deliver the box to the secure zone." \
  --agent forklift --tool deliver_to_safe_zone --json
```

The direct worker call requires you to manage delegation and revocation separately.

## Reset and Verification

After completion or failure, reset the scene from the Mac or from the server while the tunnel is open.

```bash
curl --fail -X POST http://127.0.0.1:21200/action \
  -H 'Content-Type: application/json' -d '{"action":"reset"}'
```

Reset closes the gate and returns the box and forklift to their starting positions. Reset requests are rejected while an animation is running. The bridge supports one task at a time and validates task IDs and action order. SST response failures, gate-opening verification failures, and movement failures produce an error result. On cancellation, the worker sends `abort`. If connectivity is also lost, the current animation stage may finish, so check the scene status.

```bash
curl --fail http://127.0.0.1:21200/status
gz model -m cargo_box -p
gz topic -e -n 1 -t /secure_door/state
```

The expected final position of `cargo_box` is x=1, y=3.35, z=0.3. Record both the successful SST responses and the final placement in Gazebo.
