"""collab-init --global·--comm-only를 임시 HOME에서 확인한다(실제 홈은 건드리지 않는다)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_init_extra import tree_state

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "bin" / "collab-init"
HELPERS = ["collab-init", "collab-worker", "claude-host", "notify-claude"]
GLOBAL_SKILLS = {
    ".agents/skills/plan-with-me": "skills/plan-with-me",
    ".agents/skills/collaboration-with-claude": "skills/collaboration-with-claude",
    ".claude/skills/plan-with-me": "skills/plan-with-me",
    ".claude/skills/collaboration-with-codex": "skills/collaboration-with-codex",
}


class GlobalInitTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="collab-global-")
        self.home = Path(self.directory.name).resolve() / "home dir"
        self.home.mkdir()
        self.bin_dir = self.home / ".local/bin"

    def tearDown(self):
        self.directory.cleanup()

    def run_init(self, *args, path=None, executable=INIT):
        environment = dict(os.environ, HOME=str(self.home))
        environment["PATH"] = path if path is not None else f"{self.bin_dir}:{os.environ['PATH']}"
        return subprocess.run([str(executable), *args], env=environment, capture_output=True, text=True)

    def test_global_links_skills_and_helpers_and_is_idempotent(self):
        result = self.run_init("--global")
        self.assertEqual(result.returncode, 0, result.stderr)
        for link, source in GLOBAL_SKILLS.items():
            self.assertEqual((self.home / link).readlink(), ROOT / source, link)
        for helper in HELPERS:
            self.assertEqual((self.bin_dir / helper).readlink(), ROOT / "bin" / helper, helper)
        self.assertNotIn("주의:", result.stdout)
        state = tree_state(self.home)
        again = self.run_init("--global")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("유지:", again.stdout)
        self.assertEqual(tree_state(self.home), state)

    def test_global_dry_run_changes_nothing(self):
        result = self.run_init("--global", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"ln -s {ROOT}/bin/collab-worker {self.bin_dir}/collab-worker", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_global_conflict_changes_nothing(self):
        existing = self.home / ".claude/skills/plan-with-me"
        existing.mkdir(parents=True)
        baseline = tree_state(self.home)
        result = self.run_init("--global")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(str(existing), result.stderr)
        self.assertEqual(tree_state(self.home), baseline)

    def test_global_warns_when_bin_dir_is_not_on_path(self):
        result = self.run_init("--global", path="/usr/bin:/bin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"주의: {self.bin_dir}가 PATH에 없습니다", result.stdout)

    def test_global_rejects_project_arguments(self):
        ws = self.home / "ws"
        ws.mkdir()
        for args in [["--global", str(ws)], ["--global", "--comm-dir", str(ws)],
                     ["--global", "--comm-only"]]:
            with self.subTest(args=args):
                result = self.run_init(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("사용법:", result.stderr)
        self.assertEqual(list(ws.iterdir()), [])

    def test_linked_collab_init_finds_the_real_package(self):
        self.assertEqual(self.run_init("--global").returncode, 0)
        ws = self.home / "project"
        ws.mkdir()
        result = self.run_init(str(ws), "--comm-only", executable=self.bin_dir / "collab-init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((ws / ".comm/RULES.md").readlink(), ROOT / "RULES.md")


class CommOnlyInitTest(unittest.TestCase):
    def test_comm_only_links_rules_and_excludes_without_skills(self):
        with tempfile.TemporaryDirectory(prefix="collab-comm-only-") as directory:
            ws = Path(directory).resolve() / "ws"
            ws.mkdir()
            subprocess.run(["git", "init", "-q", str(ws)], check=True)
            environment = dict(os.environ, HOME=str(Path(directory).resolve()))
            result = subprocess.run([str(INIT), str(ws), "--comm-only"], env=environment,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((ws / ".comm/RULES.md").readlink(), ROOT / "RULES.md")
            self.assertFalse((ws / ".claude").exists())
            self.assertFalse((ws / ".agents").exists())
            self.assertIn("/.comm", (ws / ".git/info/exclude").read_text())

    def test_project_init_warns_about_duplicate_global_skills(self):
        with tempfile.TemporaryDirectory(prefix="collab-dup-") as directory:
            home = Path(directory).resolve() / "home"
            home.mkdir()
            ws = Path(directory).resolve() / "ws"
            ws.mkdir()
            environment = dict(os.environ, HOME=str(home))
            subprocess.run([str(INIT), "--global"], env=environment, check=True, capture_output=True)
            result = subprocess.run([str(INIT), str(ws), "--dry-run"], env=environment,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--comm-only", result.stderr)


if __name__ == "__main__":
    unittest.main()
