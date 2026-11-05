"""Run the CI pin-guard step against pull requests in a scratch repository."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPIED = ("scripts/pin_guard.py", ".github/workflows/ci.yml", "pyproject.toml", "uv.lock",
          "package.json")
COMMIT = "547b4b1" + "0" * 33
CORE = "https://github.com/cavi-ai/mcp-core.git"


class PinGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "gitconfig").write_text(
            "[user]\n\tname = Pin Test\n\temail = pin@example.invalid\n")
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=str(self.root / "gitconfig"),
                        GIT_CONFIG_NOSYSTEM="1")
        self.repo = self.root / "repo"
        for name in COPIED:
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, self.repo / name)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def edit(self, name, old, new):
        path = self.repo / name
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def pin_core_to_git(self):
        with (self.repo / "pyproject.toml").open("a") as stream:
            stream.write(f'\n[tool.uv.sources]\n'
                         f'cavi-mcp-core = {{ git = "{CORE}", rev = "{COMMIT}" }}\n')
        with (self.repo / "uv.lock").open("a") as stream:
            stream.write(f'\n[[package]]\nname = "cavi-pin-test"\nversion = "0.1.0"\n'
                         f'source = {{ git = "{CORE}?rev={COMMIT}#{COMMIT}" }}\n')

    def pull_request(self, change):
        self.git("checkout", "-q", "-b", "change", self.base)
        change()
        self.git("add", "-A", "--", ".", ":(exclude)vendor")
        self.git("commit", "-q", "-m", "change")
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        block = workflow.split("  pin-guard:\n", 1)[1].split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in block.split("\n\n  lint:", 1)[0].splitlines())
        runner = self.root / "runner"
        runner.mkdir(exist_ok=True)
        head = self.git("rev-parse", "HEAD")
        return subprocess.run(["bash", "-e", "-c", script], cwd=self.repo, capture_output=True,
                              text=True, check=False,
                              env=dict(self.env, BASE=self.base, HEAD=head,
                                       RUNNER_TEMP=str(runner)))

    def test_git_dependency_pin_fails(self):
        result = self.pull_request(self.pin_core_to_git)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error file=pyproject.toml::", result.stdout)
        self.assertIn("::error file=uv.lock::", result.stdout)

    def test_submodule_fails(self):
        def add_submodule():
            (self.repo / ".gitmodules").write_text(
                f'[submodule "vendor/core"]\n\tpath = vendor/core\n\turl = {CORE}\n')
            self.git("update-index", "--add", "--cacheinfo", f"160000,{COMMIT},vendor/core")
        result = self.pull_request(add_submodule)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error file=.gitmodules::", result.stdout)
        self.assertIn("::error file=vendor/core::", result.stdout)

    def test_consumer_dispatch_is_never_changed(self):
        result = self.pull_request(lambda: self.edit(
            ".github/workflows/ci.yml", "repos/cavi-ai/game-art-pipeline/dispatches",
            "repos/cavi-ai/other/dispatches"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("changes the pin-consumers job", result.stdout)

    def test_main_guard_judges_a_rewritten_guard(self):
        def disable_guard():
            (self.repo / "scripts/pin_guard.py").write_text("import sys\nsys.exit(0)\n")
            self.pin_core_to_git()
        result = self.pull_request(disable_guard)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("::error file=scripts/pin_guard.py::", result.stdout)
        self.assertIn("::error file=uv.lock::", result.stdout)

    def test_dependency_range_and_other_jobs_pass(self):
        def update():
            self.edit("pyproject.toml", '"numpy>=2.0,<3"', '"numpy>=2.1,<3"')
            self.edit(".github/workflows/ci.yml", "timeout-minutes: 25", "timeout-minutes: 30")
        result = self.pull_request(update)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
