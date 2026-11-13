"""plugin 구조, thread 생성, 완료 표시 대기를 실제 모델·terminal 없이 확인한다."""
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/collaborate"
SCRIPT = SKILL / "scripts/collab.py"
LOADER = importlib.machinery.SourceFileLoader("collab_plugin_tests", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
collab = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(collab)


def run_collab(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd)


class ManifestTest(unittest.TestCase):
    def load(self, path):
        return json.loads((ROOT / path).read_text())

    def test_claude_and_codex_manifests_agree(self):
        claude = self.load(".claude-plugin/plugin.json")
        codex = self.load(".codex-plugin/plugin.json")
        for key in ("name", "version", "description"):
            self.assertEqual(claude[key], codex[key], key)
        self.assertEqual(codex["skills"], "./skills/")

    def test_marketplaces_point_at_repository_root(self):
        claude = self.load(".claude-plugin/marketplace.json")
        codex = self.load(".agents/plugins/marketplace.json")
        name = self.load(".claude-plugin/plugin.json")["name"]
        self.assertEqual([p["name"] for p in claude["plugins"]], [name])
        self.assertEqual(claude["plugins"][0]["source"], "./")
        self.assertEqual([p["name"] for p in codex["plugins"]], [name])
        self.assertEqual(codex["plugins"][0]["source"], {"source": "local", "path": "./"})

    def test_every_skill_has_matching_frontmatter_name(self):
        skills = sorted(path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(skills, ["collaborate", "plan-with-me"])
        for name in skills:
            text = (ROOT / "skills" / name / "SKILL.md").read_text()
            self.assertRegex(text, rf"\A---\nname: {name}\ndescription: .+\n---\n")


class DocsTest(unittest.TestCase):
    DOCS = [ROOT / "README.md", ROOT / "README.ko.md", SKILL / "SKILL.md", SKILL / "RULES.md",
            ROOT / "skills/plan-with-me/SKILL.md", *SKILL.glob("templates/*.md")]

    def test_relative_links_exist_and_no_removed_helpers_are_mentioned(self):
        for doc in self.DOCS:
            text = doc.read_text()
            for target in re.findall(r"\]\(([^)#]+)\)", text):
                if "://" not in target:
                    self.assertTrue((doc.parent / target).exists(), f"{doc.name}: 깨진 링크 {target}")
            for removed in ("bin/", "notify-claude", "claude-host", "collab-init", "collab-worker",
                            "collaboration-with-", "Claude socket"):
                self.assertNotIn(removed, text, f"{doc.name}: 제거된 구조 언급 {removed}")

    def test_skill_commands_use_existing_subcommands(self):
        text = (SKILL / "SKILL.md").read_text()
        used = set(re.findall(r'collab\.py" (\w[\w-]*)', text))
        self.assertEqual(used, {"thread", "status", "start", "wait", "send", "read", "close",
                                "integrate", "discard"})
        help_text = run_collab("--help").stdout
        for command in used | {"mark-closed"}:
            self.assertIn(command, help_text)

    def test_readmes_share_the_same_commands(self):
        def commands(name):
            lines = []
            for block in re.findall(r"```bash\n(.*?)```", (ROOT / name).read_text(), re.S):
                for line in block.splitlines():
                    line = re.sub(r"\s+#.*$", "", line).strip()
                    if line and not line.startswith("#"):
                        lines.append(line)
            return lines
        self.assertEqual(commands("README.md"), commands("README.ko.md"))

    def test_manifest_versions_match_readme_note(self):
        for name in ("README.md", "README.ko.md"):
            text = (ROOT / name).read_text()
            self.assertIn(".claude-plugin/plugin.json", text)
            self.assertIn(".codex-plugin/plugin.json", text)

    def test_readmes_link_each_other(self):
        self.assertIn("**English** | [한국어](README.ko.md)", (ROOT / "README.md").read_text())
        self.assertIn("[English](README.md) | **한국어**", (ROOT / "README.ko.md").read_text())


class RequestLineTest(unittest.TestCase):
    def test_request_line_names_the_absolute_rules_file(self):
        line = collab.request_line("/ws/.comm/t/01-claude-request.md")
        self.assertEqual(line, f"/ws/.comm/t/01-claude-request.md — {SKILL / 'RULES.md'} 준수")
        self.assertTrue((SKILL / "RULES.md").is_file())

    def test_recovery_hint_is_a_python_command_for_this_script(self):
        hint = collab.recovery_hint("/ws/.comm/t", "orca", "term-1")
        command = shlex.split(hint.split(": ", 1)[1])
        self.assertEqual(command[:3], ["python3", str(SCRIPT), "mark-closed"])


class ThreadTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="collab thread-")
        self.ws = Path(self.directory.name).resolve() / "my ws"
        self.ws.mkdir()

    def tearDown(self):
        self.directory.cleanup()

    def test_creates_dated_thread_and_excludes_comm_dir_once(self):
        subprocess.run(["git", "init", "-q", str(self.ws)], check=True)
        result = run_collab("thread", "--workspace", self.ws, "--topic", "fix login bug")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        thread = Path(data["thread"])
        self.assertTrue(thread.is_dir())
        self.assertEqual(thread.parent, self.ws / ".comm")
        self.assertRegex(thread.name, r"^\d{8}-fix-login-bug$")
        self.assertEqual(data["rules"], str(SKILL / "RULES.md"))
        self.assertEqual(data["templates"], str(SKILL / "templates"))
        exclude = self.ws / ".git/info/exclude"
        self.assertEqual(exclude.read_text().count("/.comm\n"), 1)
        self.assertEqual(subprocess.run(["git", "-C", str(self.ws), "status", "--porcelain"],
                                        capture_output=True, text=True).stdout, "")
        again = run_collab("thread", "--workspace", self.ws, "--topic", "other")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIsNone(json.loads(again.stdout)["git_exclude"])
        self.assertEqual(exclude.read_text().count("/.comm\n"), 1)

    def test_existing_thread_requires_flag(self):
        first = run_collab("thread", "--workspace", self.ws, "--topic", "same")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = run_collab("thread", "--workspace", self.ws, "--topic", "same")
        self.assertEqual(second.returncode, 2)
        self.assertIn("--existing", second.stderr)
        third = run_collab("thread", "--workspace", self.ws, "--topic", "same", "--existing")
        self.assertEqual(third.returncode, 0, third.stderr)

    def test_outside_git_and_custom_comm_dir(self):
        comm = Path(self.directory.name).resolve() / "comm dir"
        result = run_collab("thread", "--workspace", self.ws, "--topic", "x", "--comm-dir", comm)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(Path(data["thread"]).parent, comm)
        self.assertIsNone(data["git_exclude"])

    def test_bad_arguments(self):
        for args in [["--workspace", "relative", "--topic", "x"],
                     ["--workspace", self.ws, "--topic", "///"],
                     ["--workspace", self.ws, "--topic", "x", "--comm-dir", "relative"]]:
            with self.subTest(args=args):
                result = run_collab("thread", *args)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(list(self.ws.iterdir()), [])


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


class WorktreeTest(unittest.TestCase):
    """병렬 작업용 worktree 생성·상태·합치기·버리기를 실제 git으로 확인한다."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="collab worktree-")
        self.root = Path(self.directory.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        self.directory.cleanup()

    def thread(self, topic, *extra):
        result = run_collab("thread", "--workspace", self.repo, "--topic", topic, "--worktree", *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def commit_in(self, path, name, text):
        (Path(path) / name).write_text(text)
        git(path, "add", name)
        git(path, "commit", "-qm", f"add {name}")

    def test_thread_creates_branch_and_sibling_worktree(self):
        data = self.thread("task one")
        tree = data["worktree"]
        self.assertEqual(Path(tree["path"]).parent, self.root / "repo.worktrees")
        self.assertEqual(tree["branch"], "collab/" + Path(data["thread"]).name)
        self.assertEqual(tree["base_branch"], "main")
        self.assertFalse(tree["base_dirty"])
        self.assertEqual(data["worker_workspace"], tree["path"])
        self.assertEqual(Path(data["thread"]).parent, self.repo / ".comm")
        self.assertTrue((Path(tree["path"]) / "a.txt").is_file())
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(git(tree["path"], "status", "--porcelain"), "")  # .comm 제외가 worktree에도 적용된다

    def test_subdirectory_workspace_maps_into_worktree(self):
        (self.repo / "pkg").mkdir()
        self.commit_in(self.repo / "pkg", "b.txt", "b\n")
        result = run_collab("thread", "--workspace", self.repo / "pkg", "--topic", "sub", "--worktree")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["worker_workspace"], str(Path(data["worktree"]["path"]) / "pkg"))

    def test_refuses_outside_git_and_detached_head_without_leaving_thread(self):
        plain = self.root / "plain"
        plain.mkdir()
        result = run_collab("thread", "--workspace", plain, "--topic", "x", "--worktree")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list((plain / ".comm").iterdir()), [])
        git(self.repo, "checkout", "-q", "--detach")
        result = run_collab("thread", "--workspace", self.repo, "--topic", "y", "--worktree")
        self.assertEqual(result.returncode, 2)
        self.assertIn("detached", result.stderr)

    def test_start_requires_the_worktree_workspace(self):
        data = self.thread("start check")
        request = Path(data["thread"]) / "01-claude-request.md"
        request.write_text("req")
        result = run_collab("start", "--env", "tmux", "--worker", "codex", "--workspace", self.repo,
                            "--request", request, "--title", "t", "--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn(data["worker_workspace"], result.stderr)

    def test_integrate_rebases_onto_moved_base_and_cleans_up(self):
        first = self.thread("first")
        second = self.thread("second")
        self.commit_in(first["worktree"]["path"], "one.txt", "1\n")
        self.commit_in(second["worktree"]["path"], "two.txt", "2\n")
        for data in (first, second):
            result = run_collab("integrate", "--thread", data["thread"])
            self.assertEqual(result.returncode, 0, result.stderr)
            out = json.loads(result.stdout)
            self.assertEqual(out["status"], "integrated")
            self.assertFalse(Path(data["worktree"]["path"]).exists())
        self.assertTrue((self.repo / "one.txt").is_file() and (self.repo / "two.txt").is_file())
        self.assertEqual(git(self.repo, "rev-list", "--merges", "--count", "HEAD"), "0")  # 일직선 이력
        self.assertEqual(git(self.repo, "branch", "--list", "collab/*"), "")
        self.assertEqual(json.loads(run_collab("status", "--workspace", self.repo).stdout)["threads"], [])
        again = run_collab("integrate", "--thread", first["thread"])
        self.assertEqual(again.returncode, 2)

    def test_integrate_conflict_aborts_and_keeps_everything(self):
        data = self.thread("conflict")
        tree = data["worktree"]["path"]
        self.commit_in(tree, "a.txt", "worker\n")
        self.commit_in(self.repo, "a.txt", "base moved\n")
        before = git(tree, "rev-parse", "HEAD")
        result = run_collab("integrate", "--thread", data["thread"])
        self.assertEqual(result.returncode, 5, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["status"], "conflict")
        self.assertEqual(out["conflicts"], ["a.txt"])
        self.assertEqual(git(tree, "rev-parse", "HEAD"), before)
        self.assertEqual(git(tree, "status", "--porcelain"), "")
        self.assertEqual((self.repo / "a.txt").read_text(), "base moved\n")

    def test_integrate_refuses_uncommitted_changes_open_worker_and_empty_branch(self):
        data = self.thread("guards")
        tree = Path(data["worktree"]["path"])
        empty = run_collab("integrate", "--thread", data["thread"])
        self.assertEqual(empty.returncode, 2)
        self.assertIn("discard", empty.stderr)
        (tree / "new.txt").write_text("x")
        dirty = run_collab("integrate", "--thread", data["thread"])
        self.assertEqual(dirty.returncode, 2)
        self.assertIn("new.txt", dirty.stderr)
        record = collab.new_record(data["thread"], "tmux", "%1", "codex", tree, "/r.md")
        collab.save_record(record)
        opened = run_collab("integrate", "--thread", data["thread"])
        self.assertEqual(opened.returncode, 2)
        self.assertIn("close", opened.stderr)

    def test_integrate_when_base_branch_is_not_checked_out(self):
        data = self.thread("other branch")
        self.commit_in(data["worktree"]["path"], "x.txt", "x\n")
        git(self.repo, "checkout", "-q", "-b", "side")
        result = run_collab("integrate", "--thread", data["thread"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.repo, "show", "main:x.txt"), "x")
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD"), "side")

    def test_discard_refuses_work_without_force(self):
        data = self.thread("discard")
        tree = data["worktree"]["path"]
        self.commit_in(tree, "keep.txt", "k\n")
        refused = run_collab("discard", "--thread", data["thread"])
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(len(json.loads(refused.stdout)["unmerged_commits"]), 1)
        self.assertTrue(Path(tree).exists())
        forced = run_collab("discard", "--thread", data["thread"], "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertFalse(Path(tree).exists())
        self.assertEqual(git(self.repo, "branch", "--list", "collab/*"), "")
        self.assertFalse((self.repo / "keep.txt").exists())

    def test_status_lists_open_workers_and_active_worktrees(self):
        same = run_collab("thread", "--workspace", self.repo, "--topic", "same tree")
        same = json.loads(same.stdout)
        collab.save_record(collab.new_record(same["thread"], "tmux", "%7", "codex", self.repo, "/r.md"))
        tree = self.thread("tree")
        threads = json.loads(run_collab("status", "--workspace", self.repo).stdout)["threads"]
        by_thread = {item["thread"]: item for item in threads}
        self.assertEqual(by_thread[same["thread"]]["worker"]["handle"], "%7")
        self.assertEqual(by_thread[tree["thread"]]["worktree"]["status"], "active")


class WaitTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="collab-wait-")
        self.thread = Path(self.directory.name).resolve() / "thread"
        self.thread.mkdir()
        self.reply = self.thread / "02-codex-implementation.md"

    def tearDown(self):
        self.directory.cleanup()

    def test_returns_when_done_marker_appears(self):
        def finish():
            time.sleep(0.4)
            self.reply.write_text("reply")
            Path(str(self.reply) + ".done").touch()
        writer = threading.Thread(target=finish)
        writer.start()
        result = run_collab("wait", "--thread", self.thread, "--reply", self.reply, "--interval", "0.1")
        writer.join()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "done")
        self.assertTrue(data["reply_exists"])

    def test_existing_marker_returns_immediately_and_timeout_is_distinct(self):
        timed_out = run_collab("wait", "--thread", self.thread, "--reply", self.reply,
                               "--interval", "0.1", "--timeout", "0.3")
        self.assertEqual(timed_out.returncode, 4, timed_out.stderr)
        self.assertEqual(json.loads(timed_out.stdout)["status"], "timeout")
        Path(str(self.reply) + ".done").touch()
        done = run_collab("wait", "--thread", self.thread, "--reply", self.reply, "--timeout", "5")
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_reply_must_be_inside_thread(self):
        result = run_collab("wait", "--thread", self.thread, "--reply", "/elsewhere/02.md")
        self.assertEqual(result.returncode, 2)

    def test_unavailable_worker_wakes_host_after_two_failed_checks(self):
        record = collab.new_record(self.thread, "tmux", "%999999", "codex", self.thread, str(self.reply))
        record["identity"] = {"session": "collab-gone"}
        collab.save_record(record)
        environment = dict(os.environ, PATH="/nonexistent")  # tmux를 찾지 못해 조회가 실패한다
        result = subprocess.run([sys.executable, str(SCRIPT), "wait", "--thread", str(self.thread),
                                 "--reply", str(self.reply), "--interval", "0.05", "--check-every", "0.1",
                                 "--timeout", "10"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 3, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "worker-unavailable")
        self.assertEqual(data["handle"], "%999999")


if __name__ == "__main__":
    unittest.main()
