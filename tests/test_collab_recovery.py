"""closed worker의 원본 기록 보존과 terminal을 조작하지 않는 수동 종료 복구."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("collab_worker_recovery", str(ROOT / "skills/collaborate/scripts/collab.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)


def metadata(handle="term-next"):
    return {"ok": True, "result": {"terminal": {
        "handle": handle, "ptyId": "pty-next", "incarnationId": "incarnation-next",
        "executionHostId": "local", "worktreeId": "repo", "worktreePath": "/workspace"}},
            "_meta": {"runtimeId": "runtime-next"}}


class WorkerRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.comm = self.workspace / ".comm"
        self.comm.mkdir()
        self.thread = self.comm / "thread with spaces"
        self.thread.mkdir()
        self.request = self.thread / "01-request.md"
        self.request.write_text("approved")
        self.path = self.thread / worker.RECORD_NAME
        self.history = self.thread / worker.HISTORY_DIR
        self.env = patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": "term-host"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def seed(self, status="closed", identity=None):
        record = worker.new_record(self.thread, "orca", "term-old", "codex", self.workspace, str(self.request))
        record.update(status=status, stage=status, identity=identity)
        # Preserve unusual but valid formatting, UTF-8 and trailing whitespace exactly.
        raw = (json.dumps(record, ensure_ascii=False, separators=(", ", ": ")) + "  \n\n").encode()
        self.path.write_bytes(raw)
        return record, raw

    def args(self, **overrides):
        args = dict(env="orca", worker="codex", workspace=str(self.workspace), request=str(self.request),
                    title="restart", worker_args=[], orca_worktree=None, ready_timeout=90, dry_run=False,
                    handle="term-old", thread=str(self.thread), closed_by_user=True, reason="사용자가 원래 worker 종료 확인")
        args.update(overrides)
        return SimpleNamespace(**args)

    def capture(self, function, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            function(args)
        return json.loads(output.getvalue())

    def start(self, args=None, inspect_create=None):
        def transport(command, dry=False):
            self.assertFalse(dry)
            action = command[2]
            if action == "create":
                if inspect_create:
                    inspect_create()
                return {"ok": True, "result": {"terminal": {"handle": "term-next"}}}
            if action == "show":
                return metadata()
            if action == "wait":
                return {"ok": True}
            if action == "send":
                return {"ok": True, "result": {"send": {"accepted": True, "prompt": {
                    "requestId": "request-next", "observation": "supported", "stages": ["input_accepted"]}}}}
            self.fail("unexpected terminal operation: " + str(command))
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", side_effect=transport):
            return self.capture(worker.cmd_start, args or self.args())

    def test_closed_restart_archives_exact_raw_bytes_before_any_terminal_creation(self):
        _, raw = self.seed()
        def before_create():
            self.assertEqual(self.path.read_bytes(), raw)
            self.assertEqual([p.read_bytes() for p in self.history.iterdir()], [raw])
        result = self.start(inspect_create=before_create)
        archive = Path(result["archived_record"])
        self.assertEqual(archive.parent, self.history)
        self.assertEqual(archive.read_bytes(), raw)
        self.assertEqual(json.loads(self.path.read_bytes())["handle"], "term-next")
        self.assertEqual(json.loads(self.path.read_bytes())["status"], "open")

    def test_multiple_closed_restarts_keep_distinct_unmodified_history(self):
        _, first_raw = self.seed()
        first = self.start()
        self.capture(worker.cmd_mark_closed, self.args(handle="term-next", reason="첫 worker 직접 종료"))
        second_raw = self.path.read_bytes()
        second = self.start()
        self.assertNotEqual(first["archived_record"], second["archived_record"])
        self.assertEqual(Path(first["archived_record"]).read_bytes(), first_raw)
        self.assertEqual(Path(second["archived_record"]).read_bytes(), second_raw)
        self.assertEqual(len(list(self.history.iterdir())), 2)

    def test_open_failed_and_unknown_status_refuse_creation_with_close_guidance(self):
        for status in ("open", "failed", "unknown"):
            with self.subTest(status=status):
                _, raw = self.seed(status=status)
                with patch.object(worker, "orca_json") as transport:
                    with self.assertRaisesRegex(worker.WorkerError, "먼저 close"):
                        worker.cmd_start(self.args())
                    transport.assert_not_called()
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertFalse(self.history.exists())

    def test_closed_dry_run_changes_no_record_history_or_transport(self):
        _, raw = self.seed()
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker.subprocess, "run") as transport, contextlib.redirect_stdout(output):
            worker.cmd_start(self.args(dry_run=True))
        transport.assert_not_called()
        self.assertEqual(len(output.getvalue().splitlines()), 4)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertFalse(self.history.exists())

    def test_history_symlink_or_file_conflict_stops_before_creation(self):
        _, raw = self.seed()
        elsewhere = self.workspace / "outside"
        elsewhere.mkdir()
        for conflict in ("symlink", "file"):
            with self.subTest(conflict=conflict):
                if conflict == "symlink":
                    self.history.symlink_to(elsewhere, target_is_directory=True)
                else:
                    self.history.write_text("user file")
                with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                        patch.object(worker, "orca_json") as transport, contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(OSError):
                        worker.cmd_start(self.args())
                    transport.assert_not_called()
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertEqual(list(elsewhere.iterdir()), [])
                if conflict == "file":
                    self.assertEqual(self.history.read_text(), "user file")
                self.history.unlink()

    def test_history_filename_collision_does_not_overwrite_existing_entry(self):
        _, raw = self.seed()
        self.history.mkdir()
        occupied = self.history / "fixed-aaaaaaaaaaaa.json"
        occupied.write_bytes(b"existing history")
        with patch.object(worker, "datetime") as clock, patch.object(worker.secrets, "token_hex", return_value="aaaaaaaaaaaa"):
            clock.now.return_value.strftime.return_value = "fixed"
            with self.assertRaisesRegex(worker.WorkerError, "충돌"):
                worker.archive_record(self.thread, raw)
        self.assertEqual(occupied.read_bytes(), b"existing history")
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(len(list(self.history.iterdir())), 1)

    def test_archive_failure_keeps_old_record_and_never_creates_terminal(self):
        _, raw = self.seed()
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "archive_record", side_effect=OSError("disk full")), \
                patch.object(worker, "orca_json") as transport, contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(OSError, "disk full"):
                worker.cmd_start(self.args())
        transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), raw)
        result = json.loads(output.getvalue())
        self.assertEqual(result["stage"], "archiving")
        self.assertIsNone(result["handle"])
        self.assertIs(result["transmission"]["attempted"], False)

    def test_create_failure_preserves_current_closed_record_and_archive(self):
        _, raw = self.seed()
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", side_effect=worker.WorkerError("create failed", 1)) as transport, \
                contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(worker.WorkerError, "create failed"):
                worker.cmd_start(self.args())
        transport.assert_called_once()
        result = json.loads(output.getvalue())
        self.assertEqual(result["stage"], "creating")
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(Path(result["archived_record"]).read_bytes(), raw)
        self.assertIs(result["automatic_cleanup"], False)

    def test_mark_closed_requires_explicit_confirmation_and_nonempty_reason(self):
        _, raw = self.seed(status="failed")
        for overrides in ({"closed_by_user": False}, {"reason": ""}, {"reason": " \t\n"}):
            with self.subTest(overrides=overrides), patch.object(worker, "orca_json") as orca, \
                    patch.object(worker, "run") as tmux:
                with self.assertRaisesRegex(worker.WorkerError, "--closed-by-user"):
                    worker.cmd_mark_closed(self.args(**overrides))
                self.assertEqual(self.path.read_bytes(), raw)
                orca.assert_not_called()
                tmux.assert_not_called()

    def test_cli_missing_confirmation_or_reason_exits_two_without_mutation(self):
        _, raw = self.seed(status="failed")
        base = [str(ROOT / "skills/collaborate/scripts/collab.py"), "mark-closed", "--env", "orca", "--handle", "term-old",
                "--thread", str(self.thread)]
        for incomplete in (["--reason", "manual close"], ["--closed-by-user"]):
            result = subprocess.run([*base, *incomplete], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("usage:", result.stderr)
            self.assertEqual(self.path.read_bytes(), raw)

    def test_recovery_validates_thread_environment_handle_without_external_operations(self):
        _, raw = self.seed(status="failed")
        other = self.comm / "another-thread"
        other.mkdir()
        (other / worker.RECORD_NAME).write_bytes(raw)
        for overrides in ({"env": "tmux"}, {"handle": "foreign"}, {"thread": str(other)}):
            with self.subTest(overrides=overrides), patch.object(worker, "orca_json") as orca, \
                    patch.object(worker, "run") as tmux:
                with self.assertRaisesRegex(worker.WorkerError, "일치"):
                    worker.cmd_mark_closed(self.args(**overrides))
                orca.assert_not_called()
                tmux.assert_not_called()
        self.assertEqual(self.path.read_bytes(), raw)

    def test_recovery_records_reason_and_identity_less_record_without_touching_terminal(self):
        record, _ = self.seed(status="failed", identity=None)
        with patch.object(worker, "orca_json") as orca, patch.object(worker, "run") as tmux:
            result = self.capture(worker.cmd_mark_closed, self.args(reason="  사용자가 직접 닫음  "))
        orca.assert_not_called()
        tmux.assert_not_called()
        saved = json.loads(self.path.read_bytes())
        self.assertEqual(saved["status"], "closed")
        self.assertIsNone(saved["identity"])
        self.assertEqual(saved["closure"]["reason"], "사용자가 직접 닫음")
        self.assertEqual(saved["closure"]["method"], "user-confirmed")
        self.assertEqual(saved["transmission"], record["transmission"])
        self.assertIs(result["terminal_modified"], False)
        self.assertEqual(result["marked_closed"], "term-old")
        self.assertFalse(self.history.exists())

    def test_incomplete_identity_guides_manual_close_and_then_restart_is_possible(self):
        self.seed(status="failed", identity=None)
        with self.assertRaises(worker.WorkerError) as caught:
            worker.load_record(self.thread, "orca", "term-old")
        self.assertIn("Orca/tmux에서 직접 닫은", str(caught.exception))
        self.assertIn("mark-closed", str(caught.exception))
        self.assertIn("--closed-by-user", str(caught.exception))
        self.capture(worker.cmd_mark_closed, self.args())
        closed_raw = self.path.read_bytes()
        result = self.start()
        self.assertEqual(Path(result["archived_record"]).read_bytes(), closed_raw)

    def test_changed_runtime_guides_manual_recovery_without_closing_replacement(self):
        identity = worker.orca_identity(metadata("term-old"))
        identity["runtimeId"] = "previous-app-runtime"
        self.seed(status="open", identity=identity)
        with patch.object(worker, "orca_json", return_value=metadata("term-old")) as transport:
            with self.assertRaises(worker.WorkerError) as caught:
                worker.verify_worker(self.thread, "orca", "term-old")
            self.assertIn("바뀌", str(caught.exception))
            self.assertIn("mark-closed", str(caught.exception))
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(transport.call_args.args[0][2], "show")
        with patch.object(worker, "orca_json") as orca, patch.object(worker, "run") as tmux:
            self.capture(worker.cmd_mark_closed, self.args(reason="앱 재시작 뒤 원래 worker 종료 직접 확인"))
            orca.assert_not_called()
            tmux.assert_not_called()
        self.assertEqual(json.loads(self.path.read_bytes())["status"], "closed")

    def test_repeated_mark_closed_preserves_original_reason_and_bytes(self):
        self.seed(status="failed")
        self.capture(worker.cmd_mark_closed, self.args(reason="original reason"))
        raw = self.path.read_bytes()
        with self.assertRaisesRegex(worker.WorkerError, "기존 종료 사유"):
            worker.cmd_mark_closed(self.args(reason="replacement reason"))
        self.assertEqual(self.path.read_bytes(), raw)

    def test_new_record_after_failed_identification_reports_recovery_and_keeps_history(self):
        _, raw = self.seed()
        output = io.StringIO()
        replies = [{"ok": True, "result": {"terminal": {"handle": "term-next"}}}, {"ok": True, "result": {}}]
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", side_effect=replies), contextlib.redirect_stdout(output):
            with self.assertRaises(worker.WorkerError):
                worker.cmd_start(self.args())
        result = json.loads(output.getvalue())
        self.assertIn("mark-closed", result["recovery"])
        self.assertEqual(Path(result["archived_record"]).read_bytes(), raw)
        self.assertEqual(json.loads(self.path.read_bytes())["status"], "failed")

    def test_missing_record_after_write_failure_guides_manual_close_then_fix_storage(self):
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", return_value={"result": {"handle": "term-next"}}) as transport, \
                patch.object(worker, "save_record", side_effect=OSError("read-only filesystem")), \
                contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(OSError, "read-only"):
                worker.cmd_start(self.args())
        result = json.loads(output.getvalue())
        self.assertEqual(result["handle"], "term-next")
        self.assertIn("직접 확인하고 닫은", result["recovery"])
        self.assertIn("쓰기 오류를 해결하고 start", result["recovery"])
        self.assertNotIn("mark-closed", result["recovery"])
        self.assertFalse(self.path.exists())
        transport.assert_called_once()

    def test_recovery_symlink_record_is_refused_without_modifying_target(self):
        target = self.workspace / "user-record"
        _, raw = self.seed(status="failed")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(worker.WorkerError, "symlink"):
            worker.cmd_mark_closed(self.args())
        self.assertEqual(target.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
