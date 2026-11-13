"""실제 모델·terminal 없이 E2E 진단 보존과 host/worker 제어 분리를 검증한다."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name("e2e_roundtrip.py")
SPEC = importlib.util.spec_from_file_location("e2e_roundtrip_for_tests", SOURCE)
E2E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E2E)


class E2ECleanupTest(unittest.TestCase):
    def test_failure_and_keep_preserve_workspace_and_screens(self):
        for code, keep in [(1, False), (3, False), (0, True)]:
            with self.subTest(code=code, keep=keep), tempfile.TemporaryDirectory() as directory:
                ws = Path(directory) / "run"
                evidence = ws / ".comm" / "thread" / "02-evidence"
                evidence.mkdir(parents=True)
                reply = evidence.parent / "02-codex-implementation.md"
                reply.write_text("worker reply")
                term = Mock()
                term.screen.return_value = "원본 화면"
                term.close.return_value = True
                with patch.object(E2E, "log"):
                    self.assertEqual(E2E.finish_run(ws, evidence, [term], code, keep), code)
                self.assertEqual(reply.read_text(), "worker reply")
                self.assertEqual((evidence / "terminal-0.txt").read_text(), "원본 화면")
                term.close.assert_called_once_with()

    def test_success_cleans_workspace_but_close_failure_preserves_it(self):
        for closed in [True, False]:
            with self.subTest(closed=closed), tempfile.TemporaryDirectory() as directory:
                ws = Path(directory) / "run"
                evidence = ws / ".comm" / "thread" / "02-evidence"
                evidence.mkdir(parents=True)
                term = Mock()
                term.screen.return_value = "screen"
                term.close.return_value = closed
                with patch.object(E2E, "log"), patch("builtins.print"):
                    code = E2E.finish_run(ws, evidence, [term], 0, False)
                self.assertEqual(code, 0 if closed else 1)
                self.assertEqual(ws.exists(), not closed)

    def test_setup_failure_preserves_exception_and_run_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(E2E, "ROOT", root), patch.object(E2E.time, "strftime", return_value="test"), \
                    patch.object(E2E, "run", side_effect=RuntimeError("init failed")), \
                    patch("sys.argv", ["e2e", "--env", "orca", "--host", "claude"]), patch("builtins.print"):
                with self.assertRaises(SystemExit) as stopped:
                    E2E.main()
            self.assertEqual(stopped.exception.code, 1)
            [evidence] = (root / ".e2e" / "orca-claude-test" / ".comm").glob("*-e2e/02-evidence")
            self.assertIn("init failed", (evidence / "error.txt").read_text())
            self.assertIn("결과 코드 1", (evidence / "runner.log").read_text())
            self.assertIsNone(E2E.RUN_LOG)


class E2EHostPromptTest(unittest.TestCase):
    def test_prompt_does_not_contain_the_receipt_it_is_judged_by(self):
        thread = Path("/ws/.comm/t")
        replies = [thread / "02-codex-implementation.md", thread / "04-codex-implementation.md"]
        for reply in replies:
            prompt = E2E.claude_host_prompt(thread, reply)
            self.assertIn(str(reply), prompt)
            pattern = E2E.re.compile(r"HOST-RECEIVED\s+\S*" + E2E.re.escape(reply.name))
            self.assertIsNone(pattern.search(E2E.re.sub(r"\s*\n\s*", "", prompt)))
        self.assertLess(len(E2E.HOST_LAUNCH_PROMPT.encode()), 200)

    def test_parallel_prompt_has_one_wait_per_task_and_no_receipt(self):
        pairs = [(Path(f"/ws/.comm/{name}"), Path(f"/ws/.comm/{name}/02-codex-implementation.md"))
                 for name in ("20261113-a", "20261113-b")]
        prompt = E2E.claude_host_parallel_prompt(pairs)
        self.assertEqual(prompt.count(" wait --thread "), 2)
        flat = E2E.re.sub(r"\s*\n\s*", "", prompt)
        for _, reply in pairs:
            self.assertIn(str(reply), prompt)
            pattern = E2E.re.compile(r"HOST-RECEIVED\s+\S*" + E2E.re.escape(f"{reply.parent.name}/{reply.name}"))
            self.assertIsNone(pattern.search(flat))


class E2ETermControlTest(unittest.TestCase):
    def test_recorded_worker_uses_thread_for_read_and_close(self):
        term = E2E.Term.worker("orca", "term-worker", Path("/tmp/thread"))
        with patch.object(E2E, "run", return_value="screen") as run, patch.object(E2E, "log"):
            self.assertEqual(term.screen(), "screen")
            self.assertTrue(term.close())
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:2], E2E.COLLAB)
            self.assertEqual(command[command.index("--thread") + 1], Path("/tmp/thread"))

    def test_direct_host_uses_only_its_own_handle(self):
        term = E2E.Term.worker("orca", "term-owned-host", None)
        response = json.dumps({"result": {"terminal": {"tail": ["host screen"]}}})
        with patch.object(E2E, "run", return_value=response) as run, patch.object(E2E, "log"):
            self.assertEqual(term.screen(), "host screen")
            self.assertTrue(term.close())
        for call in run.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:2], ["orca", "terminal"])
            self.assertEqual(command[command.index("--terminal") + 1], "term-owned-host")

    def test_host_is_registered_before_a_later_start_error(self):
        terms = []
        with patch.object(E2E, "run", side_effect=["%25\n", RuntimeError("send error")]):
            with self.assertRaises(RuntimeError):
                E2E.Term("tmux", "owned-host", Path("/tmp/ws"), "host-cli", terms)
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0].handle, "%25")
        self.assertIsNone(terms[0].thread)

    def test_orca_application_error_is_a_close_failure(self):
        term = E2E.Term.worker("orca", "term-owned-host", None)
        with patch.object(E2E, "run", return_value='{"ok": false, "error": "gone"}'), patch.object(E2E, "log"):
            self.assertFalse(term.close())


if __name__ == "__main__":
    unittest.main()
