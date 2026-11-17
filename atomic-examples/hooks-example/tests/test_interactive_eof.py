"""Exercise the interactive demo with real Rich input and a closed input pipe."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("user_input", ["", "/metrics\n", "/exit\n"])
def test_interactive_demo_exits_when_input_closes(tmp_path, user_input):
    example_root = Path(__file__).resolve().parents[1]
    repository = example_root.parents[1]
    environment = dict(os.environ)
    # Construct native clients without a real credential or external requests.
    environment.update(
        OPENAI_API_KEY="unused-test-key",
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        PYTHONPATH=os.pathsep.join((str(repository / "atomic-agents"), str(example_root))),
        PYTHONIOENCODING="utf-8",
    )
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        environment.pop(name, None)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from hooks_example.main import demonstrate_interactive_mode; demonstrate_interactive_mode()",
            ],
            input=user_input,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=tmp_path,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        pytest.fail(f"Demo kept running after input closed ({output.count(b'EOF when reading a line')} repeated EOF errors)")

    assert result.returncode == 0, result.stderr
    assert "EOF when reading a line" not in result.stdout
    assert "Exiting" in result.stdout
    if user_input == "/metrics\n":
        assert "Total Requests" in result.stdout
