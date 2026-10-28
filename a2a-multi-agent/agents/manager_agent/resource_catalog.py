"""Read a deployment-selected graph as resource data, never as instructions."""
import json
import os
from pathlib import Path
from agents.common.search_models import DelegationScope


def duration_seconds(value):
    scope = DelegationScope(resource="validation", validity=value)
    count, unit = scope.validity.split("*")
    return int(count) * {"sec": 1, "min": 60, "hour": 3600, "day": 86400}[unit]


def load_resource_catalog():
    filename = os.getenv("IOTAUTH_GRAPH_FILE", "").strip()
    if not filename:
        raise ValueError("Set IOTAUTH_GRAPH_FILE to the graph saved by the GUI to enable resource selection.")
    path = Path(filename).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    try:
        graph = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read resource graph {path}: {exc}") from exc
    if not isinstance(graph, dict) or any(not isinstance(graph.get(k), list)
            for k in ("entityList", "privilegeList", "commPolicies")):
        raise ValueError("Resource graph must have entityList, privilegeList, and commPolicies arrays.")
    if any(not isinstance(row, dict) for k in ("entityList", "privilegeList", "commPolicies") for row in graph[k]):
        raise ValueError("Graph entities and policies must be objects.")
    identity = os.getenv("IOTAUTH_MANAGER_ENTITY", "net1.managerAgent")
    managers = [e for e in graph["entityList"] if e.get("name") == identity]
    if len(managers) != 1 or not isinstance(managers[0].get("group"), str):
        raise ValueError("IOTAUTH_MANAGER_ENTITY must identify exactly one entity in the graph.")
    manager_group = managers[0]["group"]
    resources = {}
    for entity in graph["entityList"]:
        if "host" not in entity or "port" not in entity:
            continue
        group = entity.get("group")
        DelegationScope(resource=group, validity="1*sec")
        record = resources.setdefault(group, dict(group=group, entities=[], delegation_options=[]))
        record["entities"].append({key: entity[key] for key in ("name", "host", "port", "description") if key in entity})
    parent_targets = {p.get("Target") for p in graph["commPolicies"]
                      if p.get("RequestingGroup") == manager_group and p.get("TargetType") == "Group"}
    for privilege in graph["privilegeList"]:
        target = privilege.get("object")
        if (privilege.get("privilegeType") != "DelegationGrant"
                or privilege.get("privilegedGroup") != manager_group
                or target not in resources or target not in parent_targets):
            continue
        validity = privilege.get("validity")
        duration_seconds(validity)
        subject = privilege.get("subject")
        DelegationScope(resource=subject, validity=validity)
        resources[target]["delegation_options"].append(dict(subject=subject, validity=validity))
    return dict(manager_entity=identity, manager_group=manager_group, resources=list(resources.values()))
