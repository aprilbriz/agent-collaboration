"""초기화 인자 오류·부모 경로 충돌이 파일을 남기지 않는지 확인한다."""
import subprocess
import tempfile
import unittest
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / "bin" / "collab-init"


def tree_state(root):
    state = {}
    for path in root.rglob("*"):
        name = str(path.relative_to(root))
        if path.is_symlink():
            state[name] = ("link", str(path.readlink()))
        elif path.is_dir():
            state[name] = ("dir",)
        else:
            state[name] = ("file", path.read_bytes())
    return state


class CollabInitErrorsTest(unittest.TestCase):
    def test_bad_arguments_return_usage_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ws = root / "workspace"
            ws.mkdir()
            cases = [[], [""], [str(ws), "--comm-dir"], [str(ws), "--comm-dir", ""],
                     [str(ws), "--comm-dir", "--dry-run"], [str(ws), "--unknown"],
                     ["--unknown", str(ws)], [str(ws), "extra"],
                     [str(ws), "--comm-dir", "relative/path"]]
            baseline = tree_state(root)
            for args in cases:
                with self.subTest(args=args):
                    result = subprocess.run([str(INIT), *args], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("사용법:", result.stderr)
                    self.assertNotIn("unbound", result.stderr)
                    self.assertEqual(tree_state(root), baseline)

    def test_every_parent_conflict_blocks_the_whole_install(self):
        for conflict in [".comm", ".agents", ".agents/skills", ".claude", ".claude/skills"]:
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ws = root / "workspace"
                ws.mkdir()
                path = ws / conflict
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("사용자 기존 파일\n")
                baseline = tree_state(root)
                result = subprocess.run([str(INIT), str(ws)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(str(path), result.stderr)
                self.assertIn("부모 경로", result.stderr)
                self.assertEqual(tree_state(root), baseline)

    def test_custom_comm_ancestor_and_dangling_parent_symlink_are_conflicts(self):
        for dangling in [False, True]:
            with self.subTest(dangling=dangling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                ws = root / "workspace"
                ws.mkdir()
                blocked = root / "target"
                if dangling:
                    blocked.symlink_to(root / "missing")
                else:
                    blocked.write_text("기존 파일\n")
                baseline = tree_state(root)
                result = subprocess.run([str(INIT), str(ws), "--comm-dir", str(blocked / "nested" / ".comm")],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(tree_state(root), baseline)


if __name__ == "__main__":
    unittest.main()
