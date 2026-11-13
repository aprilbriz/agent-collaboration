"""worker의 thread 소유권·프로세스 교체·전송 불확실성과 실패 복구 계약."""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("collab_worker_records", str(ROOT / "skills/collaborate/scripts/collab.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)


def metadata(handle="term-worker"):
    return {"ok": True, "result": {"terminal": {
        "handle": handle, "ptyId": "pty-worker", "incarnationId": "incarnation-one",
        "executionHostId": "local", "worktreeId": "repo-id", "worktreePath": "/workspace",
        "title": "A changing title", "agentIdentity": "codex"}}, "_meta": {"runtimeId": "runtime-one"}}


class WorkerRecordsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.comm = self.workspace / ".comm"
        self.comm.mkdir()
        self.thread = self.comm / "thread"
        self.thread.mkdir()
        self.request = self.thread / "01-host-request.md"
        self.request.write_text("approved")
        self.env = patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": "term-host", "TMUX_PANE": "%999"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def seed(self, env="orca", handle="term-worker", identity=None):
        record = worker.new_record(self.thread, env, handle, "codex", self.workspace, str(self.request))
        record["identity"] = identity or worker.orca_identity(metadata(handle))
        worker.save_record(record)
        return record

    def args(self, **extra):
        args = dict(env="orca", worker="codex", workspace=str(self.workspace),
                    request=str(self.request), title="job", worker_args=[], orca_worktree=None,
                    ready_timeout=90, dry_run=False, handle="term-worker", thread=None, lines=80)
        args.update(extra)
        return SimpleNamespace(**args)

    def capture(self, function, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            function(args)
        return json.loads(output.getvalue())

    def test_refuses_foreign_thread_before_transport(self):
        self.seed()
        other = self.comm / "other"
        other.mkdir()
        with patch.object(worker, "orca_json") as transport:
            with self.assertRaisesRegex(worker.WorkerError, "기록"):
                worker.verify_worker(other, "orca", "term-worker")
        transport.assert_not_called()

    def test_refuses_copied_record_and_mismatched_handle_environment(self):
        self.seed()
        for env, handle in (("orca", "term-foreign"), ("tmux", "term-worker")):
            with self.subTest(env=env, handle=handle), self.assertRaisesRegex(worker.WorkerError, "일치"):
                worker.load_record(self.thread, env, handle)
        other = self.comm / "other"
        other.mkdir()
        (other / worker.RECORD_NAME).write_text((self.thread / worker.RECORD_NAME).read_text())
        with self.assertRaisesRegex(worker.WorkerError, "일치"):
            worker.load_record(other, "orca", "term-worker")

    def test_missing_thread_or_closed_or_incomplete_identity_stops_before_transport(self):
        with self.assertRaises(worker.WorkerError):
            worker.check_thread("relative")
        for field, value in (("status", "closed"), ("identity", None)):
            record = self.seed()
            record[field] = value
            worker.save_record(record)
            with patch.object(worker, "orca_json") as transport:
                with self.assertRaises(worker.WorkerError):
                    worker.verify_worker(self.thread, "orca", "term-worker")
            transport.assert_not_called()

    def test_self_terminal_rejected_before_lookup(self):
        self.seed(handle="term-host")
        with patch.object(worker, "orca_json") as transport:
            with self.assertRaisesRegex(worker.WorkerError, "자신"):
                worker.verify_worker(self.thread, "orca", "term-host")
        transport.assert_not_called()

    def test_every_orca_instance_boundary_is_checked_without_using_title(self):
        self.seed()
        for key in ("ptyId", "incarnationId", "executionHostId", "worktreeId", "worktreePath", "runtimeId"):
            changed = metadata()
            container = changed["_meta"] if key == "runtimeId" else changed["result"]["terminal"]
            container[key] = "replacement"
            with self.subTest(key=key), patch.object(worker, "orca_json", return_value=changed):
                with self.assertRaisesRegex(worker.WorkerError, "바뀌"):
                    worker.verify_worker(self.thread, "orca", "term-worker")
        unchanged = metadata()
        unchanged["result"]["terminal"]["title"] = "worker renamed itself"
        with patch.object(worker, "orca_json", return_value=unchanged):
            self.assertEqual(worker.verify_worker(self.thread, "orca", "term-worker")["handle"], "term-worker")

    def test_incomplete_or_wrong_metadata_is_refused(self):
        self.seed()
        incomplete = metadata()
        del incomplete["result"]["terminal"]["incarnationId"]
        with patch.object(worker, "orca_json", return_value=incomplete):
            with self.assertRaisesRegex(worker.WorkerError, "부족"):
                worker.verify_worker(self.thread, "orca", "term-worker")
        with patch.object(worker, "orca_json", return_value=metadata("different")):
            with self.assertRaisesRegex(worker.WorkerError, "handle"):
                worker.verify_worker(self.thread, "orca", "term-worker")

    def test_replaced_process_cannot_send_read_or_close(self):
        self.seed()
        changed = metadata()
        changed["result"]["terminal"]["incarnationId"] = "replacement"
        for action in (worker.cmd_send, worker.cmd_read, worker.cmd_close):
            with self.subTest(action=action.__name__), patch.object(worker, "orca_json", return_value=changed) as transport:
                with self.assertRaisesRegex(worker.WorkerError, "바뀌"):
                    action(self.args(thread=str(self.thread)))
                self.assertEqual(transport.call_count, 1)
                self.assertEqual(transport.call_args.args[0][1:3], ["terminal", "show"])

    def test_explicit_send_thread_must_match_request(self):
        self.seed()
        other = self.comm / "other"
        other.mkdir()
        with patch.object(worker, "orca_json") as transport:
            with self.assertRaisesRegex(worker.WorkerError, "--thread"):
                worker.cmd_send(self.args(thread=str(other)))
        transport.assert_not_called()

    def test_record_symlink_does_not_overwrite_other_files(self):
        destination = self.workspace / "keep"
        destination.write_text("keep")
        (self.thread / worker.RECORD_NAME).symlink_to(destination)
        with self.assertRaisesRegex(worker.WorkerError, "symlink"):
            self.seed()
        self.assertEqual(destination.read_text(), "keep")

    def test_second_start_is_refused_without_creating_terminal(self):
        self.seed()
        with patch.object(worker, "orca_json") as transport:
            with self.assertRaisesRegex(worker.WorkerError, "이미"):
                worker.cmd_start(self.args())
        transport.assert_not_called()

    def test_send_receipt_unknown_does_not_claim_started_or_use_envelope_id(self):
        result = worker.receipt_summary({"id": "correlation-only", "ok": True, "result": {}}, "h", "line")
        self.assertIsNone(result["input_accepted"])
        self.assertIsNone(result["turn_started"])
        self.assertIsNone(result["confirmation_command"])
        self.assertIn("ID가 없어", result["note"])

    def test_unobserved_acceptance_outputs_safe_retry_with_exact_quoted_payload(self):
        line = "/a path/$(literal)/03.md — RULES.md 준수"
        data = {"ok": True, "result": {"receipt": {"requestId": "durable-one",
                "stages": ["input_accepted"], "observation": "supported"}}}
        result = worker.receipt_summary(data, "term-worker", line)
        command = shlex.split(result["confirmation_command"])
        self.assertIs(result["input_accepted"], True)
        self.assertIs(result["turn_started"], False)
        self.assertEqual(command[command.index("--text") + 1], line)
        self.assertEqual(command[command.index("--retry-request") + 1], "durable-one")
        self.assertEqual(command[command.index("--wait-submit") + 1], "10")

    def test_observed_started_and_old_host_are_distinct(self):
        started = worker.receipt_summary({"result": {"stages": ["input_accepted", "turn_started"]}}, "h", "l")
        self.assertIs(started["turn_started"], True)
        self.assertIsNone(started["confirmation_command"])
        old = worker.receipt_summary({"result": {"provider": "old-host", "inputAccepted": True}}, "h", "l")
        self.assertIs(old["input_accepted"], True)
        self.assertIsNone(old["turn_started"])

    def test_receipt_warning_actual_retry_id_is_allowed_but_placeholder_is_not(self):
        warning = {"warning": "confirm with --retry-request req_actual-1 --wait-submit 10"}
        result = worker.receipt_summary(warning, "h", "l")
        self.assertIn("req_actual-1", result["confirmation_command"])
        self.assertIsNone(worker.receipt_summary({"warning": "--retry-request <id>"}, "h", "l")["confirmation_command"])

    def test_send_never_automatically_retries_and_persists_original_receipt(self):
        self.seed()
        receipt = {"ok": True, "result": {"requestId": "req-one", "stages": ["input_accepted"], "observation": "supported"}}
        with patch.object(worker, "orca_json", side_effect=[metadata(), receipt]) as transport:
            result = self.capture(worker.cmd_send, self.args())
        self.assertEqual(transport.call_count, 2)
        self.assertIn("--retry-request", result["send_summary"]["confirmation_command"])
        self.assertNotIn("--retry-request", transport.call_args.args[0])
        saved = json.loads((self.thread / worker.RECORD_NAME).read_text())
        self.assertEqual(saved["send_result"], receipt)
        self.assertEqual(saved["transmission"], {"attempted": True, "input_accepted": True, "turn_started": False})

    def test_real_orca_send_shape_and_unsafe_retry_conditions(self):
        receipt = {"ok": True, "result": {"send": {"accepted": True, "prompt": {
            "requestId": "safe-request", "stages": ["input_accepted"], "provider": "codex",
            "observation": "supported"}}, "warnings": ["input accepted"]}}
        summary = worker.receipt_summary(receipt, "term-worker", "line")
        self.assertIs(summary["input_accepted"], True)
        self.assertIs(summary["turn_started"], False)
        self.assertIn("safe-request", summary["confirmation_command"])
        for provider, observation in (("old-host", "unsupported"), ("unsupported", "unsupported"),
                                      ("codex", "incarnation_replaced")):
            unsafe = copy.deepcopy(receipt)
            unsafe["result"]["send"]["prompt"].update(provider=provider, observation=observation)
            unsafe["result"]["warnings"] = ["do not retry with this request ID"]
            with self.subTest(provider=provider, observation=observation):
                self.assertIsNone(worker.receipt_summary(unsafe, "h", "line")["confirmation_command"])
        for malformed in (None, False, 123, "input_accepted"):
            malformed_receipt = copy.deepcopy(receipt)
            malformed_receipt["result"]["send"]["prompt"]["stages"] = malformed
            with self.subTest(stages=malformed):
                self.assertIsNone(worker.receipt_summary(malformed_receipt, "h", "line")["turn_started"])

    def test_receipt_payload_is_not_used_as_retry_guidance_and_rejection_never_confirms(self):
        payload = {"result": {"send": {"accepted": True, "text": "--retry-request user-text",
                   "prompt": {"observation": "supported", "stages": ["input_accepted"]}}}}
        self.assertIsNone(worker.receipt_summary(payload, "h", "line")["confirmation_command"])
        rejected = {"result": {"send": {"accepted": False, "prompt": {"requestId": "not-accepted",
                    "observation": "supported", "stages": []}}}}
        self.assertIsNone(worker.receipt_summary(rejected, "h", "line")["confirmation_command"])

    def test_tmux_own_pane_requires_matching_server_to_avoid_cross_server_collision(self):
        identity = {"socketPath": "/host/tmux", "serverPid": "42"}
        with patch.dict(os.environ, {"TMUX_PANE": "%1", "TMUX": "/host/tmux,42,0"}):
            with self.assertRaisesRegex(worker.WorkerError, "자신"):
                worker.reject_self("tmux", "%1", identity)
            worker.reject_self("tmux", "%1", dict(identity, socketPath="/isolated/test-server"))

    def test_failed_wait_keeps_instance_and_reports_no_transmission_or_cleanup(self):
        replies = [{"ok": True, "result": {"terminal": {"handle": "term-worker"}}}, metadata(),
                   worker.WorkerError("agent-update-prompt", 1, {"blockedReason": "agent-update-prompt"})]
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", side_effect=replies) as transport, \
                contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(worker.WorkerError, "agent-update"):
                worker.cmd_start(self.args())
        result = json.loads(output.getvalue())
        self.assertEqual(result["stage"], "waiting")
        self.assertEqual(result["handle"], "term-worker")
        self.assertIs(result["transmission"]["attempted"], False)
        self.assertIs(result["automatic_cleanup"], False)
        self.assertEqual(transport.call_count, 3)
        saved = json.loads((self.thread / worker.RECORD_NAME).read_text())
        self.assertEqual(saved["identity"], worker.orca_identity(metadata()))
        self.assertEqual(saved["status"], "failed")

    def test_record_write_failure_after_creation_still_reports_handle_and_does_not_close(self):
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", return_value={"result": {"handle": "term-worker"}}) as transport, \
                patch.object(worker, "save_record", side_effect=OSError("disk full")), \
                contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(OSError, "disk full"):
                worker.cmd_start(self.args())
        result = json.loads(output.getvalue())
        self.assertEqual(result["handle"], "term-worker")
        self.assertEqual(result["stage"], "created")
        self.assertEqual(result["record_error"], "disk full")
        self.assertIs(result["transmission"]["attempted"], False)
        self.assertEqual(transport.call_count, 1)

    def test_transport_failure_after_send_is_ambiguous_and_never_resends(self):
        replies = [{"ok": True, "result": {"terminal": {"handle": "term-worker"}}}, metadata(),
                   {"ok": True}, metadata(), worker.WorkerError("transport lost", 1)]
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", side_effect=replies) as transport, \
                contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(worker.WorkerError, "transport"):
                worker.cmd_start(self.args())
        result = json.loads(output.getvalue())
        self.assertEqual(result["stage"], "sending")
        self.assertEqual(result["transmission"], {"attempted": True, "input_accepted": None, "turn_started": None})
        self.assertIs(result["automatic_resend"], False)
        self.assertEqual(transport.call_count, 5)
        self.assertFalse(any(call.args[0][1:3] == ["terminal", "close"] for call in transport.call_args_list))

    def test_failed_identification_keeps_partial_record_and_handle_for_diagnosis(self):
        replies = [{"ok": True, "result": {"terminal": {"handle": "term-worker"}}}, {"ok": True, "result": {}}]
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", side_effect=replies), contextlib.redirect_stdout(output):
            with self.assertRaises(worker.WorkerError):
                worker.cmd_start(self.args())
        result = json.loads(output.getvalue())
        self.assertEqual(result["stage"], "identifying")
        self.assertEqual(result["handle"], "term-worker")
        self.assertIsNone(json.loads((self.thread / worker.RECORD_NAME).read_text())["identity"])

    def test_successful_start_preserves_receipt_and_does_not_auto_close(self):
        receipt = {"ok": True, "result": {"requestId": "r", "stages": ["input_accepted"], "observation": "supported"}}
        replies = [{"ok": True, "result": {"terminal": {"handle": "term-worker"}}}, metadata(),
                   {"ok": True}, metadata(), receipt]
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker, "orca_json", side_effect=replies) as transport:
            result = self.capture(worker.cmd_start, self.args())
        self.assertEqual(result["send_result"], receipt)
        self.assertEqual(result["record"], str(self.thread / worker.RECORD_NAME))
        self.assertEqual(transport.call_count, 5)

    def test_tmux_changed_server_or_pane_is_refused_and_close_kills_only_worker_pane(self):
        identity = {"session": "collab-one", "sessionId": "$1", "panePid": "42", "serverPid": "43"}
        self.seed(env="tmux", handle="%1", identity=identity)
        changed = dict(identity, panePid="replacement")
        with patch.object(worker, "tmux_identity", return_value=changed), patch.object(worker, "run") as transport:
            with self.assertRaisesRegex(worker.WorkerError, "바뀌"):
                worker.cmd_close(self.args(env="tmux", handle="%1", thread=str(self.thread)))
            transport.assert_not_called()
        with patch.object(worker, "tmux_identity", return_value=identity), patch.object(worker, "run") as transport:
            result = self.capture(worker.cmd_close, self.args(env="tmux", handle="%1", thread=str(self.thread)))
            transport.assert_called_once_with(["tmux", "kill-pane", "-t", "%1"], False)
        self.assertEqual(result["closed"], "%1")
        self.assertEqual(json.loads((self.thread / worker.RECORD_NAME).read_text())["status"], "closed")

    def test_orca_close_checks_instance_and_marks_record_closed(self):
        self.seed()
        with patch.object(worker, "orca_json", side_effect=[metadata(), {"ok": True}]) as transport:
            result = self.capture(worker.cmd_close, self.args(thread=str(self.thread)))
        self.assertEqual(result["closed"], "term-worker")
        self.assertEqual(transport.call_args.args[0][1:3], ["terminal", "close"])
        self.assertEqual(json.loads((self.thread / worker.RECORD_NAME).read_text())["status"], "closed")

    def test_read_uses_record_before_screen_and_respects_line_count(self):
        self.seed()
        screen = {"ok": True, "result": {"terminal": {"tail": ["one", "two", "three"]}}}
        output = io.StringIO()
        with patch.object(worker, "orca_json", side_effect=[metadata(), screen]), contextlib.redirect_stdout(output):
            worker.cmd_read(self.args(thread=str(self.thread), lines=2))
        self.assertEqual(output.getvalue(), "two\nthree\n")

    def test_dry_run_does_not_create_record_or_run_external_commands(self):
        output = io.StringIO()
        with patch.object(worker.shutil, "which", return_value="/stub/codex"), \
                patch.object(worker.subprocess, "run") as transport, contextlib.redirect_stdout(output):
            worker.cmd_start(self.args(dry_run=True))
        transport.assert_not_called()
        self.assertEqual(len(output.getvalue().splitlines()), 4)
        self.assertFalse((self.thread / worker.RECORD_NAME).exists())


if __name__ == "__main__":
    unittest.main()
