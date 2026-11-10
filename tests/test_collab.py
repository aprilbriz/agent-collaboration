"""격리 회귀: 실제 모델·사용자 terminal·Orca를 쓰지 않는다.

- notify-claude: 임시 unix socket 서버로 frame과 오류 처리를 확인한다.
- collab-worker: tmux는 TMUX_TMPDIR로 분리한 전용 tmux 서버와 stub CLI로, Orca는 --dry-run으로 확인한다.
- collab-init: 임시 git 저장소에 적용한다.
- 문서·스킬의 상대 링크와 bin/ 참조가 실제 파일과 맞는지 확인한다.
실행: python3 -m unittest discover -s tests -v
"""
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"


def sh(*cmd, env=None, check=True):
    res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env)
    if check and res.returncode != 0:
        raise AssertionError(f"{cmd} -> {res.returncode}\n{res.stdout}\n{res.stderr}")
    return res


def wait_for(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


class TmpDir(unittest.TestCase):
    def setUp(self):
        # macOS의 unix socket 경로 길이 제한을 피하려고 짧은 /tmp 경로를 쓴다.
        self.tmp = Path(tempfile.mkdtemp(prefix="collab-t-", dir="/tmp")).resolve()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_thread(self, ws: Path, rules=True) -> Path:
        comm = ws / "docs" / ".comm"
        thread = comm / "20261110-test"
        thread.mkdir(parents=True)
        if rules:
            (comm / "RULES.md").symlink_to(ROOT / "RULES.md")
        req = thread / "01-codex-request.md"
        req.write_text("Status: OPEN\n")
        return req


class NotifyClaudeTest(TmpDir):
    def serve_once(self, path):
        got = {}
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        srv.listen(1)

        def accept():
            conn, _ = srv.accept()
            data = b""
            while chunk := conn.recv(65536):
                data += chunk
            conn.close()
            srv.close()
            got["data"] = data

        t = threading.Thread(target=accept, daemon=True)
        t.start()
        return t, got

    def test_sends_single_user_frame_line(self):
        sock = self.tmp / "s.sock"
        t, got = self.serve_once(sock)
        msg = "Codex 회신 완료: /ws/docs/.comm/t/02-codex-implementation.md"
        res = sh(BIN / "notify-claude", "--socket", sock, "--message", msg)
        t.join(5)
        self.assertIn("sent:", res.stdout)
        raw = got["data"].decode("utf-8")
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(raw.count("\n"), 1)
        self.assertEqual(json.loads(raw),
                         {"type": "user", "message": {"role": "user", "content": msg}})

    def test_rejects_missing_socket_non_socket_and_empty_message(self):
        res = sh(BIN / "notify-claude", "--socket", self.tmp / "none.sock", "--message", "x", check=False)
        self.assertEqual(res.returncode, 2)
        plain = self.tmp / "plain"
        plain.write_text("")
        res = sh(BIN / "notify-claude", "--socket", plain, "--message", "x", check=False)
        self.assertEqual(res.returncode, 2)
        res = sh(BIN / "notify-claude", "--socket", plain, "--message", "  ", check=False)
        self.assertEqual(res.returncode, 2)

    def test_reports_failure_when_nobody_listens(self):
        sock = self.tmp / "dead.sock"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(sock))
        s.close()  # socket 파일만 남고 listen하지 않음(host 종료 상황)
        res = sh(BIN / "notify-claude", "--socket", sock, "--message", "x", check=False)
        self.assertEqual(res.returncode, 1)
        self.assertNotIn("sent:", res.stdout)


@unittest.skipUnless(shutil.which("tmux"), "tmux 없음")
class CollabWorkerTmuxTest(TmpDir):
    def setUp(self):
        super().setUp()
        stub_dir = self.tmp / "stubbin"
        stub_dir.mkdir()
        # 실제 claude 대신, 시작한 폴더에 argv·cwd·이후 입력을 기록하는 stub
        stub = stub_dir / "claude"
        stub.write_text(
            "#!/bin/sh\n"
            "pwd -P > .stub-cwd\n"
            "for a in \"$@\"; do printf '%s\\n' \"$a\"; done > .stub-argv\n"
            "while IFS= read -r l; do printf '%s\\n' \"$l\" >> .stub-input; done\n"
        )
        stub.chmod(0o755)
        self.env = dict(os.environ)
        self.env.pop("TMUX", None)
        self.env["TMUX_TMPDIR"] = str(self.tmp)  # 사용자 tmux 서버와 분리
        self.env["PATH"] = f"{stub_dir}:{self.env['PATH']}"

    def tearDown(self):
        sh("tmux", "kill-server", env=self.env, check=False)
        super().tearDown()

    def cw(self, *args, check=True):
        return sh(BIN / "collab-worker", *args, env=self.env, check=check)

    def start(self, ws, title, *extra):
        req = self.make_thread(ws)
        info = json.loads(self.cw("start", "--env", "tmux", "--worker", "claude", "--workspace", ws,
                                  "--request", req, "--title", title, *extra).stdout)
        self.assertTrue(wait_for((ws / ".stub-argv").exists), "stub worker가 시작되지 않음")
        time.sleep(0.3)
        return info, req

    def tmux(self, *args, check=True):
        return sh("tmux", *args, env=self.env, check=check)

    def test_start_send_read_close_bound_to_original_pane(self):
        ws = self.tmp / "w s"  # 공백 경로
        ws.mkdir()
        info, req = self.start(ws, "t1", "--", "--model", "x")
        handle, session = info["handle"], info["session"]
        self.assertRegex(handle, r"^%\d+$")
        self.assertEqual((ws / ".stub-argv").read_text().splitlines(),
                         ["--dangerously-skip-permissions", "--model", "x", f"{req} — RULES.md 준수"])
        self.assertEqual((ws / ".stub-cwd").read_text().strip(), str(ws))

        # 사용자가 같은 session에 다른 창을 열어 활성화해도 원래 worker pane으로 간다.
        self.tmux("new-window", "-t", f"={session}:", "-n", "other", "cat > /dev/null")
        self.assertNotEqual(self.tmux("display-message", "-p", "-t", f"={session}:", "#{pane_id}").stdout.strip(),
                            handle)
        req2 = req.parent / "03-codex-request.md"
        req2.write_text("Status: OPEN\n")
        self.cw("send", "--env", "tmux", "--handle", handle, "--request", req2)
        input_file = ws / ".stub-input"
        self.assertTrue(wait_for(lambda: input_file.exists() and "03-codex" in input_file.read_text()))
        self.assertEqual(input_file.read_text().splitlines()[-1], f"{req2} — RULES.md 준수")
        self.assertIn("03-codex-request.md", self.cw("read", "--env", "tmux", "--handle", handle).stdout)

        self.cw("close", "--env", "tmux", "--handle", handle)
        self.assertNotEqual(self.tmux("has-session", "-t", f"={session}", check=False).returncode, 0)

    def test_same_title_in_two_workspaces_gets_separate_workers(self):
        infos = []
        for name in ["project one", "project two"]:
            ws = self.tmp / name
            ws.mkdir()
            info, _ = self.start(ws, "구현")
            infos.append(info)
            self.assertEqual((ws / ".stub-cwd").read_text().strip(), str(ws))
        self.assertNotEqual(infos[0]["handle"], infos[1]["handle"])
        self.assertNotEqual(infos[0]["session"], infos[1]["session"])
        for info in infos:
            self.assertTrue(info["session"].startswith("collab-project-"), info["session"])
            self.cw("close", "--env", "tmux", "--handle", info["handle"])

    def test_refuses_foreign_pane(self):
        pane = self.tmux("new-session", "-d", "-P", "-F", "#{pane_id}", "-s", "user-work").stdout.strip()
        for cmd in (["close"], ["read"]):
            res = self.cw(*cmd, "--env", "tmux", "--handle", pane, check=False)
            self.assertEqual(res.returncode, 2)
        res = self.cw("close", "--env", "tmux", "--handle", "user-work", check=False)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self.tmux("has-session", "-t", "=user-work", check=False).returncode, 0)

    def test_preflight_errors(self):
        ws = self.tmp / "ws"
        ws.mkdir()
        req = self.make_thread(ws, rules=False)
        res = self.cw("start", "--env", "tmux", "--worker", "claude", "--workspace", ws,
                      "--request", req, "--title", "t2", check=False)
        self.assertEqual(res.returncode, 2)
        self.assertIn("RULES.md", res.stderr)
        res = self.cw("start", "--env", "tmux", "--worker", "claude", "--workspace", ws,
                      "--request", "docs/x.md", "--title", "t2", check=False)
        self.assertEqual(res.returncode, 2)


class CollabWorkerOrcaDryRunTest(TmpDir):
    def test_command_sequence(self):
        ws = self.tmp / "my ws"
        ws.mkdir()
        req = self.make_thread(ws)
        res = sh(BIN / "collab-worker", "start", "--env", "orca", "--worker", "codex",
                 "--workspace", ws, "--request", req, "--title", "job", "--dry-run")
        cmds = [shlex.split(l[2:]) for l in res.stdout.splitlines()]
        self.assertEqual(len(cmds), 3)
        create, wait, send = cmds
        self.assertEqual(create[:5], ["orca", "terminal", "create", "--title", "job"])
        self.assertEqual(create[create.index("--command") + 1], f"cd {shlex.quote(str(ws))} && codex --dangerously-bypass-approvals-and-sandbox")
        self.assertEqual(wait[wait.index("--for") + 1], "tui-idle")
        self.assertEqual(send[:5], ["orca", "terminal", "send", "--terminal", "<handle>"])
        self.assertEqual(send[send.index("--text") + 1], f"{req} — RULES.md 준수")
        self.assertIn("--enter", send)
        self.assertEqual(send[send.index("--wait-submit") + 1], "10")


class CollabInitTest(TmpDir):
    def test_links_and_local_excludes_idempotently(self):
        ws = self.tmp / "proj"
        ws.mkdir()
        sh("git", "init", "-q", ws)
        sh(BIN / "collab-init", ws)
        self.assertEqual((ws / "docs/.comm/RULES.md").resolve(), ROOT / "RULES.md")
        for p in [".agents/skills/plan-with-me", ".agents/skills/collaboration-with-claude",
                  ".claude/skills/plan-with-me", ".claude/skills/collaboration-with-codex"]:
            self.assertTrue((ws / p / "SKILL.md").is_file(), p)
        self.assertFalse((ws / ".agents/skills/collaboration-with-codex").exists())
        self.assertFalse((ws / ".claude/skills/collaboration-with-claude").exists())
        again = sh(BIN / "collab-init", ws)
        self.assertIn("유지", again.stdout)
        exclude = (ws / ".git/info/exclude").read_text().splitlines()
        added = [l for l in exclude if l.startswith("/")]
        self.assertEqual(sorted(added), sorted(set(added)), "중복 exclude")
        self.assertIn("/docs/.comm", added)
        self.assertEqual(sh("git", "-C", ws, "status", "--porcelain").stdout.strip(), "",
                         "연결한 경로가 git status에 보이면 안 됨")

    def test_existing_rules_or_skill_blocks_whole_install(self):
        ws = self.tmp / "proj"
        (ws / "docs/.comm").mkdir(parents=True)
        (ws / "docs/.comm/RULES.md").write_text("기존 규칙\n")
        res = sh(BIN / "collab-init", ws, check=False)
        self.assertEqual(res.returncode, 1)
        self.assertIn("RULES.md", res.stderr)
        self.assertNotIn("완료", res.stdout)
        self.assertEqual((ws / "docs/.comm/RULES.md").read_text(), "기존 규칙\n")
        self.assertFalse((ws / ".agents").exists(), "충돌 시 host skill을 설치하면 안 됨")
        self.assertFalse((ws / ".claude").exists())

        ws2 = self.tmp / "proj2"
        (ws2 / ".claude/skills/plan-with-me").mkdir(parents=True)  # 사용자가 이미 가진 skill
        res = sh(BIN / "collab-init", ws2, check=False)
        self.assertEqual(res.returncode, 1)
        self.assertFalse((ws2 / "docs").exists())
        self.assertFalse((ws2 / ".claude/skills/plan-with-me").is_symlink())

    def test_workspace_then_separate_target_comm_dir(self):
        # host skill 흐름: workspace 적용 뒤 대상 저장소의 comm dir을 따로 선택한 경우
        ws = self.tmp / "workspace"
        ws.mkdir()
        target = self.tmp / "target repo"
        target.mkdir()
        sh("git", "init", "-q", target)
        sh(BIN / "collab-init", ws)
        comm = target / "docs/.comm"
        again = sh(BIN / "collab-init", ws, "--comm-dir", comm)
        self.assertIn("유지", again.stdout)
        self.assertEqual((comm / "RULES.md").resolve(), ROOT / "RULES.md")
        self.assertIn("/docs/.comm", (target / ".git/info/exclude").read_text())
        thread = comm / "20261110-x"
        thread.mkdir()
        req = thread / "01-codex-request.md"
        req.write_text("Status: OPEN\n")
        res = sh(BIN / "collab-worker", "start", "--env", "orca", "--worker", "claude", "--workspace", ws,
                 "--request", req, "--title", "x", "--dry-run")
        self.assertIn(f"cd {shlex.quote(str(ws))} && claude", res.stdout.replace("'\"'\"'", "'"))

    def test_separate_comm_dir_and_non_git_workspace(self):
        ws = self.tmp / "ws"
        ws.mkdir()
        comm = self.tmp / "target" / "docs" / ".comm"
        sh(BIN / "collab-init", ws, "--comm-dir", comm)
        self.assertTrue((comm / "RULES.md").is_symlink())
        self.assertTrue((ws / ".claude/skills/plan-with-me").is_symlink())

    def test_dry_run_changes_nothing(self):
        ws = self.tmp / "ws"
        ws.mkdir()
        sh(BIN / "collab-init", ws, "--dry-run")
        self.assertEqual(list(ws.iterdir()), [])


class SpacePathDocumentedCommandsTest(TmpDir):
    """README·skill·요청서 양식에 적힌 인용 방식 그대로 셸에서 실행해 공백 경로를 확인한다."""

    def test_quoted_invocations_with_spaces(self):
        pkg = self.tmp / "pkg dir"
        for part in ["bin", "skills", "templates"]:
            shutil.copytree(ROOT / part, pkg / part, symlinks=True)
        shutil.copy(ROOT / "RULES.md", pkg / "RULES.md")
        ws = self.tmp / "my ws"
        ws.mkdir()
        sh("git", "init", "-q", ws)
        env = dict(os.environ, PKG=str(pkg), WS=str(ws))
        sh("sh", "-c", '"$PKG/bin/collab-init" "$WS"', env=env)
        rules = ws / "docs/.comm/RULES.md"
        self.assertEqual(rules.resolve(), (pkg / "RULES.md").resolve())
        # host skill이 쓰는 <pkg> = realpath(RULES.md)의 부모에 helper가 있어야 한다.
        for helper in ["collab-worker", "notify-claude", "collab-init", "claude-host"]:
            self.assertTrue((rules.resolve().parent / "bin" / helper).is_file(), helper)

        sock = self.tmp / "sock dir" / "s.sock"
        sock.parent.mkdir()
        t, got = NotifyClaudeTest.serve_once(self, sock)
        reply = ws / "docs/.comm/t/02-codex-implementation.md"
        env.update(SOCK=str(sock), REPLY=str(reply))
        sh("sh", "-c", '"$PKG/bin/notify-claude" --socket "$SOCK" --message "Codex 회신 완료: $REPLY"', env=env)
        t.join(5)
        self.assertEqual(json.loads(got["data"])["message"]["content"], f"Codex 회신 완료: {reply}")

        thread = ws / "docs/.comm/20261110-t"
        thread.mkdir()
        req = thread / "01-claude-request.md"
        req.write_text("Status: OPEN\n")
        env["REQ"] = str(req)
        res = sh("sh", "-c", '"$PKG/bin/collab-worker" start --env orca --worker codex '
                 '--workspace "$WS" --request "$REQ" --title "my job" --dry-run', env=env)
        create = shlex.split(res.stdout.splitlines()[0][2:])
        self.assertEqual(create[create.index("--command") + 1],
                         f"cd {shlex.quote(str(ws))} && codex --dangerously-bypass-approvals-and-sandbox")


class DocsConsistencyTest(unittest.TestCase):
    def test_relative_links_and_bin_references_exist(self):
        docs = [ROOT / "README.md", ROOT / "RULES.md", *ROOT.glob("skills/*/SKILL.md"),
                *ROOT.glob("templates/*.md")]
        for doc in docs:
            text = doc.read_text()
            for target in re.findall(r"\]\(([^)#]+)\)", text):
                if "://" in target:
                    continue
                self.assertTrue((doc.parent / target).exists(), f"{doc.name}: 깨진 링크 {target}")
            for name in re.findall(r"bin/([a-z-]+)", text):
                self.assertTrue((BIN / name).is_file(), f"{doc.name}: 없는 helper bin/{name}")

    def test_host_skills_name_their_counterpart_notify_path(self):
        claude_host = (ROOT / "skills/collaboration-with-codex/SKILL.md").read_text()
        codex_host = (ROOT / "skills/collaboration-with-claude/SKILL.md").read_text()
        self.assertIn("notify-claude", claude_host)
        self.assertIn("--worker codex", claude_host)
        self.assertIn("codex queue", codex_host)
        self.assertIn("--worker claude", codex_host)


if __name__ == "__main__":
    unittest.main()
