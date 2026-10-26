import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import agent_graph_gui
from agent_graph_gui import build_graph, save_graph


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.entities = [dict(name="Manager Agent", group="ManagerAgents", kind="Agent"),
                         dict(name="robot agent", group="RobotAgents", kind="Agent"),
                         dict(name="Resource", group="Resources", kind="Resource", host="localhost", port="21101")]
        self.grant = dict(type="DelegationGrant", actor="ManagerAgents", subject="RobotAgents", target="Resources", validity="1*day")

    def test_delegation_generates_revoke_and_actor_access(self):
        graph = build_graph(self.entities, [self.grant])
        self.assertEqual(graph["commPolicies"], [dict(RequestingGroup="ManagerAgents",
            TargetType="Group", Target="Resources", MaxNumSessionKeyOwners=2,
            SessionCryptoSpec="AES-128-CBC:SHA256", AbsoluteValidity="1*day",
            RelativeValidity="6*hour", Expiration="Infinity", IsDelegated=0, Context=None)])
        self.assertEqual(graph["privilegeList"][1], dict(privilegeType="DelegationRevoke",
            privilegedGroup="ManagerAgents", subject="RobotAgents", object="Resources"))
        self.assertEqual(graph["entityList"][0]["targetServerInfoList"][0]["port"], 21101)
        self.assertEqual(graph["privilegeList"][0]["privilegedGroup"], "ManagerAgents")
        self.assertEqual(graph["entityList"][1]["targetServerInfoList"][0]["port"], 21101)
        self.assertEqual(graph["assignments"]["net1.managerAgent"], 101)
        self.assertEqual(len(graph["assignments"]), len(graph["entityList"]))

    def test_direct_and_revoke(self):
        direct = dict(self.grant, type="DirectAccess", subject="", validity="6*hour")
        revoke = dict(self.grant, type="DelegationRevoke", validity="")
        graph = build_graph(self.entities, [direct, revoke])
        self.assertEqual(graph["commPolicies"][0]["IsDelegated"], 0)
        self.assertNotIn("info", graph["privilegeList"][0])
        self.assertEqual(graph["entityList"][1]["targetServerInfoList"], [])

    def test_revoke_alone_generates_actor_policy(self):
        graph = build_graph(self.entities, [dict(self.grant, type="DelegationRevoke", validity="")])
        self.assertEqual(len(graph["privilegeList"]), 1)
        self.assertEqual(graph["commPolicies"][0]["RelativeValidity"], "6*hour")
        self.assertEqual(graph["entityList"][1]["targetServerInfoList"], [])

    def test_explicit_revoke_and_access_deduplicate_in_any_order(self):
        revoke = dict(self.grant, type="DelegationRevoke", validity="")
        direct = dict(self.grant, type="DirectAccess", subject="", validity="2*hour")
        for rules in ([self.grant, revoke, direct], [direct, revoke, self.grant]):
            with self.subTest(rules=rules):
                graph = build_graph(self.entities, rules)
                self.assertEqual(len(graph["privilegeList"]), 2)
                self.assertEqual(len(graph["commPolicies"]), 1)
                self.assertEqual(graph["commPolicies"][0]["AbsoluteValidity"], "2*hour")
                self.assertEqual(graph["commPolicies"][0]["RelativeValidity"], "2*hour")

    def test_shared_policy_and_removal(self):
        other = dict(self.grant, subject="ManagerAgents", validity="2*day")
        graph = build_graph(self.entities, [self.grant, other])
        self.assertEqual(len(graph["privilegeList"]), 4)
        self.assertEqual(len(graph["commPolicies"]), 1)
        graph = build_graph(self.entities, [other])
        self.assertEqual(len(graph["privilegeList"]), 2)
        self.assertEqual(len(graph["commPolicies"]), 1)
        graph = build_graph(self.entities, [])
        self.assertEqual(graph["privilegeList"], [])
        self.assertEqual(graph["commPolicies"], [])
        self.assertEqual(graph["entityList"][0]["targetServerInfoList"], [])

    def test_invalid_rules(self):
        for changes in (dict(subject="Missing"), dict(actor="Missing"), dict(target="Missing"), dict(validity="forever")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_graph(self.entities, [dict(self.grant, **changes)])
        with self.assertRaises(ValueError):
            build_graph(self.entities, [self.grant, self.grant])

    def test_invalid_entities(self):
        for row in (dict(self.entities[0]), dict(self.entities[2], name="Other"),
                    dict(self.entities[2], name="Other", port="70000"),
                    dict(self.entities[0], name="../bad")):
            with self.subTest(row=row), self.assertRaises(ValueError):
                build_graph(self.entities + [row], [])

    def test_safe_output_and_overwrite(self):
        graph = build_graph(self.entities, [self.grant])
        with tempfile.TemporaryDirectory() as directory:
            path = save_graph(graph, "team.graph", directory)
            self.assertTrue(path.is_file())
            with self.assertRaises(FileExistsError):
                save_graph(graph, "team.graph", directory)
            save_graph(graph, "team.graph", directory, overwrite=True)
            for name in ("../bad.graph", "/tmp/bad.graph", "bad.json"):
                with self.assertRaises(ValueError):
                    save_graph(graph, name, directory)
            link = Path(directory) / "link.graph"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                save_graph(graph, link.name, directory, overwrite=True)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="graph builder ")
        self.addCleanup(self.temp.cleanup)
        self.examples = Path(self.temp.name)
        self.configs = self.examples / "configs"
        self.configs.mkdir()
        self.graph = self.configs / "my_agents.graph"
        self.graph.write_text("{}")
        for name in ("cleanAll.sh", "generateAll.sh"):
            (self.examples / name).write_text("#!/bin/sh\nexit 0\n")
        config_patch = patch.object(agent_graph_gui, "CONFIG_DIR", self.configs)
        config_patch.start()
        self.addCleanup(config_patch.stop)

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_cleanup_then_generate_with_password(self, popen):
        pipeline = agent_graph_gui.start_generation(self.graph, "test-secret")
        popen.assert_called_once_with(["./cleanAll.sh"], cwd=str(self.examples))
        popen.return_value.poll.return_value = None
        self.assertIsNone(pipeline.poll())
        self.assertEqual(popen.call_count, 1)
        popen.return_value.poll.return_value = 0
        self.assertIsNone(pipeline.poll())
        popen.assert_called_with(["./generateAll.sh", "-g", "configs/my_agents.graph", "-p", "test-secret"], cwd=str(self.examples))
        self.assertIsNone(pipeline._password)
        self.assertEqual(pipeline.poll(), 0)
        self.assertEqual(popen.call_count, 2)

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_cleanup_failure_stops_generation(self, popen):
        popen.return_value.poll.return_value = 2
        pipeline = agent_graph_gui.start_generation(self.graph, "test-secret")
        self.assertEqual(pipeline.poll(), 2)
        self.assertEqual(pipeline.stage, "cleanAll.sh")
        self.assertIsNone(pipeline._password)
        self.assertEqual(popen.call_count, 1)

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_generation_start_failure(self, popen):
        pipeline = agent_graph_gui.start_generation(self.graph, "test-secret")
        popen.return_value.poll.return_value = 0
        popen.side_effect = OSError("launch failed")
        self.assertEqual(pipeline.poll(), 1)
        self.assertEqual(pipeline.stage, "generateAll.sh")
        self.assertIsNone(pipeline._password)

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_validation_precedes_cleanup(self, popen):
        for password in ("", "abc", "has space", "wild*card", "wild?card", "br[acket", "null\x00byte"):
            with self.subTest(password=password), self.assertRaises(ValueError):
                agent_graph_gui.start_generation(self.graph, password)
        for graph in (self.examples / "outside.graph", self.configs / "missing.graph"):
            with self.assertRaises(ValueError):
                agent_graph_gui.start_generation(graph, "test-secret")
        (self.examples / "generateAll.sh").unlink()
        with self.assertRaises(ValueError):
            agent_graph_gui.start_generation(self.graph, "test-secret")
        popen.assert_not_called()


class AuthServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auth server ")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.directory = root / "auth/auth-server"
        self.directory.mkdir(parents=True)
        (self.directory / "Makefile").write_text("all:\n")
        (self.directory.parent / "properties").mkdir()
        (self.directory.parent / "properties/exampleAuth101.properties").write_text("")
        (self.directory / "target").mkdir()
        self.jar = self.directory / "target/auth-server-jar-with-dependencies.jar"
        self.jar.write_text("")
        for patcher in (patch.object(agent_graph_gui, "CONFIG_DIR", root / "examples/configs"),
                        patch.object(agent_graph_gui.sys, "stdin")):
            patcher.start()
            self.addCleanup(patcher.stop)
        agent_graph_gui.sys.stdin.isatty.return_value = True

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_make_then_exact_java_command(self, popen):
        pipeline = agent_graph_gui.start_auth_server()
        popen.assert_called_once_with(["make"], cwd=str(self.directory))
        popen.return_value.poll.return_value = None
        self.assertIsNone(pipeline.poll())
        self.assertEqual(popen.call_count, 1)
        popen.return_value.poll.return_value = 0
        self.assertIsNone(pipeline.poll())
        popen.assert_called_with(["java", "-jar", "target/auth-server-jar-with-dependencies.jar",
                                 "-p", "../properties/exampleAuth101.properties"], cwd=str(self.directory))
        self.assertEqual(pipeline.poll(), 0)

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_build_failure_does_not_launch_java(self, popen):
        popen.return_value.poll.return_value = 2
        pipeline = agent_graph_gui.start_auth_server()
        self.assertEqual(pipeline.poll(), 2)
        self.assertEqual(popen.call_count, 1)

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_missing_jar_does_not_launch_java(self, popen):
        self.jar.unlink()
        popen.return_value.poll.return_value = 0
        pipeline = agent_graph_gui.start_auth_server()
        self.assertEqual(pipeline.poll(), 1)
        self.assertIn("JAR", pipeline.error)
        self.assertEqual(popen.call_count, 1)

    @patch.object(agent_graph_gui.subprocess, "Popen")
    def test_requires_terminal_and_config(self, popen):
        agent_graph_gui.sys.stdin.isatty.return_value = False
        with self.assertRaisesRegex(ValueError, "terminal"):
            agent_graph_gui.start_auth_server()
        agent_graph_gui.sys.stdin.isatty.return_value = True
        (self.directory.parent / "properties/exampleAuth101.properties").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            agent_graph_gui.start_auth_server()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
