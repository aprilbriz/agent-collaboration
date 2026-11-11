"""Host diagnostics and launcher checks; all process/Claude calls use stubs."""
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "bin/claude-host"
FUNCTIONS = runpy.run_path(str(HOST))


class HostArgDetectionTest(unittest.TestCase):
    def test_only_claude_inline_accept_is_detected(self):
        detect = FUNCTIONS["host_status"]
        cases = {
            'claude --settings {"crossSessionInbound":"accept"}': "accept_arg_detected",
            '/bin/claude --settings {crossSessionInbound:accept}': "accept_arg_detected",
            "claude --settings '{\"crossSessionInbound\": \"accept\"}'": "accept_arg_detected",
            '/bin/node /pkg/@anthropic-ai/claude-code/cli.js --settings={crossSessionInbound:accept}': "accept_arg_detected",
            'echo --settings {crossSessionInbound:accept}': "unknown",
            'claude --settings {crossSessionInbound:prompt}': "not_detected",
            'claude --settings {crossSessionInbound:acceptLater}': "not_detected",
            'claude --settings {othercrossSessionInbound:accept}': "not_detected",
            'claude --settings {nested:{crossSessionInbound:accept}}': "unknown",
            'claude --settings /workspace/settings.json': "unknown",
            'claude --settings {crossSessionInbound:accept} --settings /settings.json': "unknown",
            'claude --settings {crossSessionInbound:accept} --settings {crossSessionInbound:deny}': "not_detected",
            'claude --append-system-prompt crossSessionInbound:accept': "not_detected",
            'claude': "not_detected",
            '': "unknown",
        }
        for args, expected in cases.items():
            with self.subTest(args=args):
                self.assertEqual(detect(args), expected)


class HostLauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="collab-host-")
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "work space '$() `literal`"
        self.workspace.mkdir()
        self.stubbin = self.root / "bin"
        self.stubbin.mkdir()
        self.env = dict(os.environ, PATH=f"{self.stubbin}:{os.environ['PATH']}")
        self.env.pop("ORCA_CLAUDE_AGENT_VIEW_PLUGIN_DIR", None)
        self.make_stub("ps", 'import os\nprint(os.environ.get("STUB_ARGS", "claude"))\n')
        self.make_stub("claude", 'import json, os, sys\nprint(json.dumps({"argv":sys.argv[1:], "cwd":os.getcwd()}))\n')

    def tearDown(self):
        self.tmp.cleanup()

    def make_stub(self, name, source):
        path = self.stubbin / name
        path.write_text("#!/usr/bin/env python3\n" + source)
        path.chmod(0o755)

    def diagnostic(self):
        result = subprocess.run([str(HOST), "--check-host", "123", "--workspace", str(self.workspace)],
                                env=self.env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_diagnostic_does_not_launch_claude_and_command_omits_unset_plugin(self):
        # Make accidental execution visible as a non-JSON result/failure.
        self.make_stub("claude", 'raise SystemExit("must not restart during diagnosis")\n')
        self.env["STUB_ARGS"] = 'claude --settings {"crossSessionInbound":"accept"}'
        output = self.diagnostic()
        self.assertEqual(output["status"], "accept_arg_detected")
        self.assertEqual(shlex.split(output["restart_command"]),
                         ["cd", str(self.workspace), "&&", str(HOST.resolve()), "--continue"])

    def test_restart_command_quotes_workspace_and_configured_plugin(self):
        plugin = str(self.root / "plugin '$HOME' `literal` $(none)")
        self.env["ORCA_CLAUDE_AGENT_VIEW_PLUGIN_DIR"] = plugin
        output = self.diagnostic()
        self.assertEqual(output["status"], "not_detected")
        self.assertEqual(shlex.split(output["restart_command"]),
                         ["cd", str(self.workspace), "&&", str(HOST.resolve()), "--continue", "--plugin-dir", plugin])
        # Execute only against the stub CLI to verify quoting and original pass-through.
        result = subprocess.run(["sh", "-c", output["restart_command"]], env=self.env,
                                capture_output=True, text=True, check=True)
        actual = json.loads(result.stdout)
        self.assertEqual(actual["cwd"], str(self.workspace))
        self.assertEqual(actual["argv"], ["--settings", '{"crossSessionInbound":"accept"}',
                                          "--continue", "--plugin-dir", plugin])

    def test_normal_arguments_pass_through_unchanged(self):
        argv = ["--model", "example", "prompt with spaces", "--settings", "custom.json"]
        result = subprocess.run([str(HOST), *argv], env=self.env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["argv"],
                         ["--settings", '{"crossSessionInbound":"accept"}', *argv])

    def test_unreadable_process_is_unknown(self):
        self.make_stub("ps", "raise SystemExit(1)\n")
        self.assertEqual(self.diagnostic()["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
