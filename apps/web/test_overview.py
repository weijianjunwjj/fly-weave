"""Run the dependency-free Overview component contract tests through Node's test runner."""

from pathlib import Path
import shutil
import subprocess


def test_overview_component_contract() -> None:
    web_root = Path(__file__).resolve().parent
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the Flyweave web test suite"

    completed = subprocess.run(
        [node, "--test", "overview.test.mjs"],
        cwd=web_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, (
        "Overview component tests failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
