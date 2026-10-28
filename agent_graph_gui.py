#!/usr/bin/env python3
"""Build IoTAuth graphs using a Tk GUI or the reusable build_graph API."""
import json
import os
import tempfile
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_DIR = Path(__file__).resolve().parent / "iotauth" / "examples" / "configs"
ENV_FILE = Path(__file__).resolve().parent / "a2a-multi-agent" / ".env"
CRYPTO = "AES-128-CBC:SHA256"


def identifier(value):
    """Convert a display name (Manager Agent) to a safe ID (managerAgent)."""
    words = value.strip().split()
    if not words or not all(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", w) for w in words):
        raise ValueError("Names must start with a letter and contain only letters, numbers, and spaces.")
    return words[0][0].lower() + words[0][1:] + "".join(w[0].upper() + w[1:] for w in words[1:])


def port_number(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise ValueError("Ports must be integers from 1 to 65535.") from None
    if not 1 <= number <= 65535:
        raise ValueError("Ports must be integers from 1 to 65535.")
    return number


def duration(value):
    if not re.fullmatch(r"[1-9][0-9]*\*(sec|min|hour|day)", value):
        raise ValueError("Validity must look like 30*min, 6*hour, or 1*day.")
    return value


def host_name(value):
    if not value or not re.fullmatch(r"[A-Za-z0-9.:_-]+", value):
        raise ValueError("Enter a host name or IP address, without a scheme or spaces.")
    return value


def build_graph(entities, rules, network="net1", auth_host="localhost", auth_id=101):
    """Entities: name, group, kind (Agent/Resource), host, port, optional agent url.

    Rules: type (DelegationGrant/DelegationRevoke/DirectAccess), actor,
    subject, target (group names), validity. Grants imply matching revoke rights;
    all delegation privileges imply actor access with 1*day / 6*hour validity.
    Explicit DirectAccess rules override automatic communication policy validity.
    """
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", network):
        raise ValueError("Network must start with a letter and contain only letters and numbers.")
    auth_host = host_name(auth_host)
    try:
        auth_id = int(auth_id)
    except (ValueError, TypeError):
        raise ValueError("Auth ID must be a positive integer.") from None
    if not 1 <= auth_id <= 2147483647:
        raise ValueError("Auth ID must be a positive 32-bit integer.")
    if not entities:
        raise ValueError("Add at least one agent or resource.")
    graph = dict(authList=[dict(id=auth_id, entityHost=auth_host, authHost=auth_host,
                 tcpPort=21900, udpPort=21902, authPort=21901, callbackPort=21903,
                 dbProtectionMethod=1, backupEnabled=False, contextualCallbackEnabled=True)],
                 authTrusts=[], assignments={}, entityList=[], filesharingLists=[],
                 privilegeList=[], commPolicies=[])
    agents, resources, names, endpoints = set(), set(), set(), set()
    for row in entities:
        local = identifier(row["name"])
        name = network + "." + local
        if name.lower() in names:
            raise ValueError("Duplicate entity ID: " + name)
        names.add(name.lower())
        group = row["group"].strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", group):
            raise ValueError("Groups must start with a letter and contain letters, numbers, or underscores.")
        if row["kind"] not in ("Agent", "Resource"):
            raise ValueError("Entity kind must be Agent or Resource.")
        entity = dict(group=group, name=name, distProtocol="TCP", usePermanentDistKey=True,
                      distKeyValidityPeriod="1*hour", maxSessionKeysPerRequest=5,
                      netName=network, credentialPrefix=network[0].upper()+network[1:]+"."+local,
                      distributionCryptoSpec=dict(cipher="AES-128-CBC", mac="SHA256"),
                      sessionCryptoSpec=dict(cipher="AES-128-CBC", mac="SHA256"), backupToAuthIds=[])
        if row["kind"] == "Resource":
            resources.add(group)
            description = row.get("description", "").strip()
            if len(description) > 4000:
                raise ValueError("Resource descriptions must be at most 4000 characters.")
            if description:
                entity["description"] = description
            entity.update(host=host_name(row["host"].strip()), port=port_number(row["port"]),
                          maxSessionKeysPerRequest=1)
            endpoint = (entity["host"].lower(), entity["port"])
            if endpoint in endpoints or (endpoint[0] == auth_host.lower() and endpoint[1] in (21900, 21901, 21903)):
                raise ValueError("Resource endpoint conflicts with another resource or Auth TCP port.")
            endpoints.add(endpoint)
        else:
            agents.add(group)
            url = row.get("url", "").strip()
            if url:
                try:
                    parsed = urlsplit(url)
                    port = parsed.port
                    valid = (parsed.scheme in ("http", "https") and parsed.hostname
                             and not parsed.username and not parsed.password
                             and not parsed.query and not parsed.fragment
                             and not any(c.isspace() for c in url)
                             and (port is None or 1 <= port <= 65535))
                except ValueError:
                    valid = False
                if not valid:
                    raise ValueError("Agent URL must be an HTTP(S) endpoint without credentials, query parameters, or fragments.")
            entity.update(targetServerInfoList=[], contextList=[])
        graph["assignments"][name] = auth_id
        graph["entityList"].append(entity)
    if agents & resources:
        raise ValueError("Agent groups and resource groups must be distinct.")
    seen, targets = set(), {group: set() for group in agents}
    privileges, policies, explicit_policies = {}, {}, {}

    def communication_policy(actor, target, absolute="1*day", relative="6*hour"):
        return dict(RequestingGroup=actor, TargetType="Group", Target=target,
                    MaxNumSessionKeyOwners=2, SessionCryptoSpec=CRYPTO,
                    AbsoluteValidity=absolute, RelativeValidity=relative,
                    Expiration="Infinity", IsDelegated=0, Context=None)

    for rule in rules:
        kind, actor, subject, target = (rule[k] for k in ("type", "actor", "subject", "target"))
        if kind not in ("DelegationGrant", "DelegationRevoke", "DirectAccess"):
            raise ValueError("Unknown rule type: " + kind)
        if actor not in agents or target not in resources:
            raise ValueError("Rules must reference an existing agent group and resource group.")
        if kind != "DirectAccess" and subject not in agents:
            raise ValueError("Select an existing recipient agent group.")
        key = (kind, actor, subject if kind != "DirectAccess" else "", target)
        if key in seen:
            raise ValueError("Duplicate rule: " + str(key))
        seen.add(key)
        if kind == "DirectAccess":
            validity = duration(rule["validity"])
            explicit_policies[actor, target] = communication_policy(actor, target, validity, validity)
            targets[actor].add(target)
        else:
            privilege = dict(privilegeType=kind, privilegedGroup=actor, subject=subject, object=target)
            if kind == "DelegationGrant":
                validity = duration(rule["validity"])
                privilege.update(validity=validity, info=dict(cryptoSpec=CRYPTO,
                                 absValidity=validity, relValidity=validity))
                targets[subject].add(target)
            privileges[kind, actor, subject, target] = privilege
            if kind == "DelegationGrant":
                privileges.setdefault(("DelegationRevoke", actor, subject, target),
                    dict(privilegeType="DelegationRevoke", privilegedGroup=actor,
                         subject=subject, object=target))
            policies.setdefault((actor, target), communication_policy(actor, target))
            targets[actor].add(target)
    policies.update(explicit_policies)
    graph["commPolicies"] = list(policies.values())
    graph["privilegeList"] = list(privileges.values())
    for entity in graph["entityList"]:
        if "targetServerInfoList" in entity:
            entity["targetServerInfoList"] = [dict(name=e["name"], group=e["group"], host=e["host"], port=e["port"])
                for e in graph["entityList"] if "host" in e and e["group"] in targets[entity["group"]]]
    return graph



def prepare_agent_env(entities, path=ENV_FILE):
    """Merge entered agent endpoints into dotenv without storing them in the graph."""
    updates, groups = {}, {}
    for row in entities:
        if row["kind"] != "Agent" or not row.get("url", "").strip():
            continue
        name = identifier(row["name"])
        if name.endswith("Agent"):
            name = name[:-5]
        role = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()
        key = role + "_URL"
        url = row["url"].strip()
        # Validation is also performed by build_graph before GUI saves.
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(c.isspace() or c in "\\\"'$`" for c in url)
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError("Agent URL must be a plain HTTP(S) endpoint without credentials or query parameters.")
        group = row["group"].strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", group):
            raise ValueError("Invalid agent group for environment configuration.")
        if key in updates or url.rstrip("/") in {u.rstrip("/") for u in groups}:
            raise ValueError("Agents must have distinct URL variable names and endpoints.")
        updates[key] = url
        groups[url] = group
    if not updates:
        return None
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing to overwrite an environment-file symbolic link.")
    source = path if path.exists() else path.with_name(".env.example")
    text = source.read_text(encoding="utf-8") if source.exists() else ""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)(?:\r?\n)?$")
    existing = {}
    def value(raw):
        raw = raw.strip()
        if raw.startswith(("'", '"')):
            end = raw.rfind(raw[0])
            if end <= 0:
                raise ValueError("Malformed quoted environment setting.")
            return raw[1:end]
        return raw.split(" #", 1)[0].strip()
    for line in lines:
        match = pattern.match(line)
        if match and match[1] in {*updates, "IOTAUTH_AGENT_GROUPS"}:
            existing[match[1]] = value(match[2])
    try:
        mapping = json.loads(existing.get("IOTAUTH_AGENT_GROUPS", "{}"))
    except ValueError as exc:
        raise ValueError("Existing IOTAUTH_AGENT_GROUPS must contain valid JSON.") from exc
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("Existing IOTAUTH_AGENT_GROUPS must map URLs to group names.")
    old_urls = {existing[k].rstrip("/") for k in updates if k in existing}
    new_urls = {u.rstrip("/") for u in groups}
    mapping = {u: group for u, group in mapping.items() if u.rstrip("/") not in old_urls | new_urls}
    mapping.update(groups)
    updates["IOTAUTH_AGENT_GROUPS"] = "'" + json.dumps(mapping, separators=(",", ":")) + "'"
    output, written = [], set()
    for line in lines:
        match = pattern.match(line)
        if match and match[1] in updates:
            key = match[1]
            if key not in written:
                output.append(key + "=" + updates[key] + "\n")
                written.add(key)
        else:
            output.append(line)
    if output and not output[-1].endswith("\n"):
        output[-1] += "\n"
    output.extend(key + "=" + val + "\n" for key, val in updates.items() if key not in written)
    return "".join(output)


def save_agent_env(content, path=ENV_FILE):
    if content is None:
        return
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing to overwrite an environment-file symbolic link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_graph(graph, filename, directory=CONFIG_DIR, overwrite=False):
    """Restrict output to a simple .graph filename in the configs directory."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.graph", filename):
        raise ValueError("Use a filename such as my_agents.graph (letters, numbers, underscores, hyphens).")
    directory = Path(directory)
    path = directory / filename
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symbolic link.")
    payload = json.dumps(graph, indent=4) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    with path.open("w" if overwrite else "x", encoding="utf-8") as stream:
        stream.write(payload)
    return path



def validate_password(password):
    if len(password) < 4:
        raise ValueError("Enter a credential password with at least four characters.")
    # generateAll.sh expands its password variable without shell quotes.
    if any(c.isspace() or c in "*?[]\x00" for c in password):
        raise ValueError("The existing generation script cannot pass passwords containing "
                         "whitespace, *, ?, [, ], or null characters. Choose another password.")


class GenerationPipeline:
    """Advance from cleanup to generation without blocking the GUI event loop."""
    def __init__(self, graph_path, password):
        self.stage = "cleanAll.sh"
        self._password = password
        self._graph_name = graph_path.name
        self._cwd = str(CONFIG_DIR.parent)
        self._process = subprocess.Popen(["./cleanAll.sh"], cwd=self._cwd)
        self._result = None

    def poll(self):
        if self._result is not None:
            return self._result
        code = self._process.poll()
        if code is None:
            return None
        if code != 0 or self.stage == "generateAll.sh":
            self._password = None
            self._result = code
            return code
        self.stage = "generateAll.sh"
        try:
            self._process = subprocess.Popen(
                ["./generateAll.sh", "-g", "configs/" + self._graph_name, "-p", self._password],
                cwd=self._cwd)
        except OSError:
            self._result = 1
            return 1
        finally:
            self._password = None
        return None


def start_generation(graph_path, password):
    """Validate inputs before cleanup; pass GUI password via the existing -p option."""
    validate_password(password)
    graph_path = Path(graph_path)
    if graph_path.is_symlink() or graph_path.resolve().parent != CONFIG_DIR.resolve():
        raise ValueError("Select a graph saved in " + str(CONFIG_DIR))
    if graph_path.suffix != ".graph" or not graph_path.is_file():
        raise ValueError("Save the graph before running generation.")
    for name in ("cleanAll.sh", "generateAll.sh"):
        script = CONFIG_DIR.parent / name
        if not script.is_file():
            raise ValueError("Required script was not found: " + str(script))
    return GenerationPipeline(graph_path, password)



class AuthServerPipeline:
    """Build first, then let Java request its password on the launch terminal."""
    def __init__(self, directory):
        self.stage = "make"
        self._cwd = str(directory)
        self._process = subprocess.Popen(["make"], cwd=self._cwd)
        self._result = None
        self.error = None

    def poll(self):
        if self._result is not None:
            return self._result
        code = self._process.poll()
        if code is None:
            return None
        if code != 0 or self.stage == "Auth server":
            self._result = code
            return code
        self.stage = "Auth server"
        jar = Path(self._cwd) / "target/auth-server-jar-with-dependencies.jar"
        if not jar.is_file():
            self.error = "Build finished but the Auth server JAR was not found: " + str(jar)
            self._result = 1
            return 1
        try:
            self._process = subprocess.Popen(
                ["java", "-jar", "target/auth-server-jar-with-dependencies.jar",
                 "-p", "../properties/exampleAuth101.properties"], cwd=self._cwd)
        except OSError as error:
            self.error = "Cannot launch Auth server: " + str(error)
            self._result = 1
            return 1
        return None


def start_auth_server():
    directory = CONFIG_DIR.parent.parent / "auth" / "auth-server"
    for path in (directory / "Makefile", directory.parent / "properties/exampleAuth101.properties"):
        if not path.is_file():
            raise ValueError("Required Auth server file is missing: " + str(path) +
                             ". Generate the Auth101 configuration before starting the server.")
    if sys.stdin is None or not sys.stdin.isatty():
        raise ValueError("Launch the GUI from a terminal with python3 agent_graph_gui.py. "
                         "The Auth server uses that terminal to request its password and accept commands.")
    return AuthServerPipeline(directory)


def main():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
        from tkinter.scrolledtext import ScrolledText
    except ImportError:
        raise SystemExit("Tkinter is required. Run with a Python installation that includes Tk support.")

    class Builder:
        def __init__(self, root):
            self.root, self.entities, self.rules = root, [], []
            self.generation_process = None
            self.auth_process = None
            root.title("IoTAuth Agent Graph Builder")
            root.geometry("1050x800")
            root.bind("<Escape>", lambda event: root.attributes("-fullscreen", False))
            root.bind("<F11>", lambda event: root.attributes(
                "-fullscreen", not bool(root.attributes("-fullscreen"))))
            outer = ttk.Frame(root, padding=16)
            outer.pack(fill="both", expand=True)
            ttk.Label(outer, text="Agent graph builder", font=("TkDefaultFont", 20, "bold")).pack(anchor="w")
            ttk.Label(outer, text="Define agents and resources, then specify group-based access and delegation.").pack(anchor="w", pady=(4, 12))
            settings = ttk.Frame(outer)
            settings.pack(fill="x")
            self.settings = self.fields(settings, [("Network", "net1"), ("Auth host", "localhost"), ("Auth ID", "101"), ("Filename", "my_agents.graph")])
            password_row = ttk.Frame(outer)
            password_row.pack(fill="x", pady=(8, 0))
            ttk.Label(password_row, text="Credential password").pack(side="left")
            self.password = ttk.Entry(password_row, show="*", width=28)
            self.password.pack(side="left", padx=8)
            ttk.Label(password_row, text="Enter the password for Auth").pack(side="left")
            footer = ttk.Frame(outer)
            footer.pack(side="bottom", fill="x")
            tabs = ttk.Notebook(outer)
            tabs.pack(fill="both", expand=True, pady=12)
            entity_tab, rule_tab, preview_tab = [ttk.Frame(tabs, padding=12) for _ in range(3)]
            for frame, title in zip((entity_tab, rule_tab, preview_tab), ("1. Agents and resources", "2. Privileges", "3. Graph preview")):
                tabs.add(frame, text=title)
            ttk.Label(entity_tab, text="Names such as Manager Agent become net1.managerAgent. Shared groups share privileges.").pack(anchor="w")
            form = ttk.Frame(entity_tab)
            form.pack(fill="x", pady=12)
            self.entity_fields = {}
            form.columnconfigure(3, weight=1)
            for column, (key, label, width) in enumerate([
                    ("Kind", "Kind", 10), ("Name", "Name", 18),
                    ("Group", "Group", 18), ("URL", "Agent URL", 34)]):
                field_label = ttk.Label(form, text=label)
                field_label.grid(row=0, column=column, sticky="w", padx=4)
                if key == "Kind":
                    entry = ttk.Combobox(form, values=("Agent", "Resource"), state="readonly", width=width)
                    entry.set("Agent")
                else:
                    entry = ttk.Entry(form, width=width)
                entry.grid(row=1, column=column, sticky="ew", padx=4)
                self.entity_fields[key] = entry
                if key == "URL":
                    self.agent_url_label = field_label
            ttk.Label(entity_tab, text="Add human users as Agent entities.").pack(anchor="w")
            resource_holder = ttk.Frame(entity_tab)
            resource_holder.pack(fill="x")
            resource_holder.columnconfigure(0, weight=1)
            self.resource_fields_frame = ttk.Frame(resource_holder)
            self.resource_fields_frame.grid(row=0, column=0, sticky="ew")
            endpoint_row = ttk.Frame(self.resource_fields_frame)
            endpoint_row.pack(fill="x", pady=(8, 0))
            endpoint_row.columnconfigure(2, weight=1)
            for column, (key, label, value, width) in enumerate([
                    ("Host", "Host", "localhost", 24),
                    ("Port", "Port", "21100", 8),
                    ("Description", "Description (optional)", "", 40)]):
                ttk.Label(endpoint_row, text=label).grid(row=0, column=column, sticky="w", padx=4)
                entry = ttk.Entry(endpoint_row, width=width)
                entry.insert(0, value)
                entry.grid(row=1, column=column, sticky="ew", padx=4)
                self.entity_fields[key] = entry
            self.entity_fields["Kind"].bind("<<ComboboxSelected>>", self.update_entity_fields)
            self.update_entity_fields()
            self.entity_tree = self.table(entity_tab, ("kind", "name", "group", "url", "host", "port"))
            self.buttons(entity_tab, [("Add entity", self.add_entity), ("Remove selected", self.remove_entity)])
            ttk.Label(rule_tab, text="Grant/revoke: actor controls recipient access to a resource. Direct access: actor can access the resource.", wraplength=900).pack(anchor="w")
            form = ttk.Frame(rule_tab)
            form.pack(fill="x", pady=12)
            self.rule_fields = self.fields(form, [("Type", ("DelegationGrant", "DelegationRevoke", "DirectAccess")), ("Delegator", ()), ("Delegatee", ()), ("Resource", ()), ("Validity", "1*day")])
            ttk.Label(rule_tab, text="Grant adds matching revoke rights. Grant/revoke adds actor access (1*day / 6*hour). See Graph preview.").pack(anchor="w")
            self.rule_tree = self.table(rule_tab, ("type", "actor", "subject", "target", "validity"))
            self.buttons(rule_tab, [("Add rule", self.add_rule), ("Remove selected", self.remove_rule)])
            self.preview = ScrolledText(preview_tab, wrap="none", font=("TkFixedFont", 11))
            self.preview.pack(fill="both", expand=True)
            self.preview.configure(state="disabled")
            self.buttons(preview_tab, [("Refresh preview", self.refresh_preview)])
            actions = ttk.Frame(footer)
            actions.pack(fill="x")
            ttk.Button(actions, text="Save", command=lambda: self.handle(self.save)).pack(side="left", padx=(0, 8))
            self.generate_button = ttk.Button(actions, text="Save the file and generate SST",
                                             command=lambda: self.handle(self.generate))
            self.generate_button.pack(side="left", padx=(0, 8))
            self.auth_button = ttk.Button(actions, text="Run Auth server", command=lambda: self.handle(self.run_auth_server))
            self.auth_button.pack(side="left")
            self.status = tk.StringVar(value="Output directory: " + str(CONFIG_DIR))
            ttk.Label(footer, textvariable=self.status, wraplength=980).pack(anchor="w", pady=(8, 0))
            tabs.bind("<<NotebookTabChanged>>", lambda e: self.refresh_preview() if tabs.index(tabs.select()) == 2 else None)

        def update_entity_fields(self, event=None):
            if self.entity_fields["Kind"].get() == "Resource":
                self.resource_fields_frame.grid()
                self.agent_url_label.grid_remove()
                self.entity_fields["URL"].grid_remove()
            else:
                self.resource_fields_frame.grid_remove()
                self.agent_url_label.grid()
                self.entity_fields["URL"].grid()

        def fields(self, frame, specs):
            result = {}
            for col, (label, value) in enumerate(specs):
                frame.columnconfigure(col, weight=1)
                ttk.Label(frame, text=label).grid(row=0, column=col, sticky="w", padx=4)
                widget = ttk.Combobox(frame, values=value, state="readonly", width=17) if isinstance(value, tuple) else ttk.Entry(frame, width=19)
                if isinstance(value, tuple):
                    if value:
                        widget.set(value[0])
                else:
                    widget.insert(0, value)
                widget.grid(row=1, column=col, sticky="ew", padx=4)
                result[label] = widget
            return result

        def table(self, parent, columns):
            frame = ttk.Frame(parent)
            frame.pack(fill="both", expand=True, pady=12)
            tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
            for col in columns:
                tree.heading(col, text="Agent URL" if col == "url" else col.title())
                tree.column(col, width=150, minwidth=80)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            tree.pack(fill="both", expand=True)
            return tree

        def buttons(self, parent, actions):
            bar = ttk.Frame(parent)
            bar.pack(fill="x")
            for label, action in actions:
                ttk.Button(bar, text=label, command=lambda fn=action: self.handle(fn)).pack(side="left", padx=(0, 8))

        def handle(self, action):
            try:
                action()
            except (ValueError, OSError) as error:
                messagebox.showerror("Cannot complete action", str(error), parent=self.root)

        def graph(self, entities=None, rules=None):
            s = {k: v.get().strip() for k, v in self.settings.items()}
            return build_graph(self.entities if entities is None else entities,
                               self.rules if rules is None else rules, s["Network"], s["Auth host"], s["Auth ID"])

        def redraw(self):
            for tree, rows in ((self.entity_tree, self.entities), (self.rule_tree, self.rules)):
                tree.delete(*tree.get_children())
                for i, row in enumerate(rows):
                    tree.insert("", "end", iid=str(i), values=[row.get(k, "") for k in tree["columns"]])
            for field, kind in (("Delegator", "Agent"), ("Delegatee", "Agent"), ("Resource", "Resource")):
                values = sorted({r["group"] for r in self.entities if r["kind"] == kind})
                widget = self.rule_fields[field]
                widget.configure(values=values)
                if widget.get() not in values:
                    widget.set(values[0] if values else "")

        def add_entity(self):
            row = {k.lower(): v.get().strip() for k, v in self.entity_fields.items()}
            if row["kind"] == "Agent":
                row.update(host="", port="", description="")
            else:
                row["url"] = ""
            if not row["group"]:
                name = identifier(row["name"])
                row["group"] = name[0].upper() + name[1:] + "s"
            self.graph(entities=self.entities + [row])
            self.entities.append(row)
            self.redraw()
            self.entity_fields["Name"].delete(0, "end")
            self.entity_fields["URL"].delete(0, "end")
            self.entity_fields["Group"].delete(0, "end")
            self.entity_fields["Description"].delete(0, "end")

        def remove_entity(self):
            selected = self.entity_tree.selection()
            if selected:
                remaining = [r for i, r in enumerate(self.entities) if i != int(selected[0])]
                groups = {r["group"] for r in remaining}
                if any(r[k] not in groups for r in self.rules for k in ("actor", "target") ) or any(r["subject"] not in groups for r in self.rules if r["type"] != "DirectAccess"):
                    raise ValueError("Remove privilege rules referencing this entity's group first.")
                self.entities = remaining
                self.redraw()

        def add_rule(self):
            f = {k: v.get().strip() for k, v in self.rule_fields.items()}
            row = dict(type=f["Type"], actor=f["Delegator"], subject=f["Delegatee"] if f["Type"] != "DirectAccess" else "", target=f["Resource"], validity=f["Validity"])
            self.graph(rules=self.rules + [row])
            self.rules.append(row)
            self.redraw()

        def remove_rule(self):
            selected = self.rule_tree.selection()
            if selected:
                del self.rules[int(selected[0])]
                self.redraw()

        def refresh_preview(self):
            try:
                content = json.dumps(self.graph(), indent=4)
            except ValueError as error:
                content = str(error)
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", content)
            self.preview.configure(state="disabled")

        def save(self, notify=True):
            graph = self.graph()
            env_content = prepare_agent_env(self.entities)
            filename = self.settings["Filename"].get().strip()
            if not filename.endswith(".graph"):
                filename += ".graph"
            try:
                path = save_graph(graph, filename)
            except FileExistsError:
                if not messagebox.askyesno("Replace graph?", "Replace existing file " + filename + "?", parent=self.root):
                    return
                path = save_graph(graph, filename, overwrite=True)
            save_agent_env(env_content)
            saved_message = str(path)
            if env_content is not None:
                saved_message += "\nAgent URLs updated in " + str(ENV_FILE)
            self.status.set("Saved: " + saved_message)
            self.refresh_preview()
            if notify:
                messagebox.showinfo("Configuration saved", saved_message, parent=self.root)
            return path

        def generate(self):
            if self.auth_process is not None:
                raise ValueError("Stop the Auth server with quit in its terminal before cleaning and generating configurations.")
            if self.generation_process is not None:
                return
            password = self.password.get()
            validate_password(password)
            path = self.save(notify=False)
            if path is None:
                return
            self.generation_process = start_generation(path, password)
            self.password.delete(0, "end")
            self.generate_button.configure(state="disabled")
            self.auth_button.configure(state="disabled")
            self.status.set("Running cleanAll.sh before generating " + path.name + ". Output is in the launch terminal.")
            self.root.after(250, self.poll_generation)

        def poll_generation(self):
            code = self.generation_process.poll()
            if code is None:
                self.status.set("Running " + self.generation_process.stage + ". Output is in the launch terminal.")
                self.root.after(250, self.poll_generation)
                return
            completed_stage = self.generation_process.stage
            self.generation_process = None
            self.generate_button.configure(state="normal")
            self.auth_button.configure(state="normal")
            if code == 0:
                self.status.set("generateAll.sh completed successfully. See the terminal for output.")
                messagebox.showinfo("Generation complete", "generateAll.sh completed successfully.", parent=self.root)
            else:
                self.status.set(completed_stage + " failed (exit code " + str(code) + "). See the terminal for details.")
                messagebox.showerror("Generation failed", self.status.get(), parent=self.root)


        def run_auth_server(self):
            if self.auth_process is not None:
                return
            if self.generation_process is not None:
                raise ValueError("Wait for configuration generation to finish before starting Auth.")
            self.auth_process = start_auth_server()
            self.auth_button.configure(state="disabled")
            self.generate_button.configure(state="disabled")
            self.status.set("Building Auth server with make. Build output is in the launch terminal.")
            self.root.after(250, self.poll_auth_server)

        def poll_auth_server(self):
            code = self.auth_process.poll()
            if code is None:
                if self.auth_process.stage == "Auth server":
                    self.status.set("Auth server process active. Enter the Auth password when prompted in the launch terminal; use quit to stop.")
                self.root.after(250, self.poll_auth_server)
                return
            stage = self.auth_process.stage
            error = self.auth_process.error
            self.auth_process = None
            self.auth_button.configure(state="normal")
            self.generate_button.configure(state="normal")
            if code == 0:
                self.status.set("Auth server stopped.")
            else:
                self.status.set(error or stage + " failed (exit code " + str(code) + "). See the launch terminal for details.")
                messagebox.showerror("Auth server failed", self.status.get(), parent=self.root)

    root = tk.Tk()
    Builder(root)
    root.mainloop()


if __name__ == "__main__":
    main()
