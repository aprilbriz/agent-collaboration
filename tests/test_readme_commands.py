"""Run documented setup syntax and check the tmux viewing command without live sessions."""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("worker_readme_tests", str(ROOT / "bin/collab-worker"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
WORKER = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(WORKER)


# 언어별 README와 그 안의 대상 저장소 placeholder, tmux attach 안내 문구.
READMES = {
    "README.md": ("<target repo>", "the `attach` command from the start output"),
    "README.ko.md": ("<대상 저장소>", "start 출력의 `attach` 명령"),
}


def bash_blocks(name):
    return re.findall(r"```bash\n(.*?)```", (ROOT / name).read_text(), re.S)


def normalized_commands(name):
    """주석·placeholder·사유 문자열을 지운 명령 줄. 언어와 무관하게 같아야 한다."""
    commands = []
    for block in bash_blocks(name):
        for line in block.replace("\\\n", "").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            line = re.sub(r"\s+#.*$", "", line)
            line = re.sub(r"<[^>]*>", "<>", line)
            line = re.sub(r'--reason "[^"]*"', '--reason ""', line)
            commands.append(" ".join(line.split()))
    return commands


class ReadmeCommandsTest(unittest.TestCase):
    def test_documented_separate_comm_command_uses_target_dot_comm(self):
        for name, (target_placeholder, _) in READMES.items():
            with self.subTest(readme=name):
                self.check_separate_comm_command(name, target_placeholder)

    def test_language_versions_link_each_other_and_share_commands(self):
        self.assertIn("**English** | [한국어](README.ko.md)", (ROOT / "README.md").read_text())
        self.assertIn("[English](README.md) | **한국어**", (ROOT / "README.ko.md").read_text())
        self.assertEqual(normalized_commands("README.md"), normalized_commands("README.ko.md"))

    def check_separate_comm_command(self, name, target_placeholder):
        setup = next(block for block in bash_blocks(name) if "--comm-dir" in block)
        command = next(line for line in setup.replace("\\\n", "").splitlines()
                       if "--comm-dir" in line and not line.lstrip().startswith("#"))
        command = command.replace("<workspace>", "$README_WORKSPACE")
        command = command.replace(target_placeholder, "$README_TARGET")
        with tempfile.TemporaryDirectory(prefix="collab-readme-") as directory:
            ws = Path(directory).resolve() / "workspace space"
            target = Path(directory).resolve() / "target space"
            ws.mkdir()
            target.mkdir()
            environment = dict(os.environ, PKG=str(ROOT), README_WORKSPACE=str(ws), README_TARGET=str(target))
            result = subprocess.run(["sh", "-c", command + " --dry-run"],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(target / ".comm/RULES.md"), result.stdout)
            self.assertNotIn(str(target / "docs/.comm"), result.stdout)
            self.assertEqual(list(ws.iterdir()), [])
            self.assertEqual(list(target.iterdir()), [])

    def test_documented_attach_key_is_a_usable_tmux_command(self):
        for name, (_, attach_phrase) in READMES.items():
            self.assertIn(attach_phrase, (ROOT / name).read_text(), name)
        with tempfile.TemporaryDirectory(prefix="collab-attach-") as directory:
            ws = Path(directory).resolve()
            thread = ws / ".comm" / "thread"
            thread.mkdir(parents=True)
            (thread.parent / "RULES.md").symlink_to(ROOT / "RULES.md")
            request = thread / "01-codex-request.md"
            request.write_text("Status: OPEN\n")
            args = SimpleNamespace(workspace=str(ws), request=str(request), env="tmux", worker="claude",
                                   worker_args=[], title="docs", dry_run=False)
            output = io.StringIO()
            with patch.object(WORKER.shutil, "which", return_value="/stub/claude"), \
                    patch.object(WORKER, "run", return_value="%888\n"), \
                    patch.object(WORKER, "tmux_identity", return_value={"session": "collab-docs"}), \
                    patch.object(WORKER, "tmux_type"), contextlib.redirect_stdout(output):
                WORKER.cmd_start(args)
            result = json.loads(output.getvalue())
            self.assertEqual(shlex.split(result["attach"]),
                             ["tmux", "attach", "-t", "=" + result["session"]])


if __name__ == "__main__":
    unittest.main()
