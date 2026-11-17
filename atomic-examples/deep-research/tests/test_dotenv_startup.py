"""Follow the documented .env setup in an isolated copy of the example."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def example(tmp_path):
    example_root = Path(__file__).resolve().parents[1]
    shutil.copytree(example_root / "deep_research", tmp_path / "deep_research", ignore=shutil.ignore_patterns("__pycache__"))
    environment = dict(os.environ)
    for name in (
        "OPENAI_API_KEY",
        "SEARXNG_BASE_URL",
        "SEARXNG_API_KEY",
        "PYTHON_DOTENV_DISABLED",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        environment.pop(name, None)
    environment.update(
        PYTHONPATH=str(example_root.parents[1] / "atomic-agents"),
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        PYTHONIOENCODING="utf-8",
    )
    return tmp_path, environment


def write_dotenv(directory):
    (directory / ".env").write_text(
        "OPENAI_API_KEY=unused-dotenv-key\n" "SEARXNG_BASE_URL=http://127.0.0.1:18888\n" "SEARXNG_API_KEY=unused-search-key\n",
        encoding="utf-8",
    )


def read_configuration(example):
    directory, environment = example
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from deep_research.config import ChatConfig; "
            "print(json.dumps([ChatConfig.api_key, ChatConfig.searxng_base_url, ChatConfig.searxng_api_key]))",
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_documented_dotenv_settings_are_loaded_before_configuration(example):
    write_dotenv(example[0])
    result = read_configuration(example)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["unused-dotenv-key", "http://127.0.0.1:18888", "unused-search-key"]


def test_process_environment_keeps_precedence_over_dotenv(example):
    write_dotenv(example[0])
    example[1].update(
        OPENAI_API_KEY="unused-process-key",
        SEARXNG_BASE_URL="http://127.0.0.1:19999",
        SEARXNG_API_KEY="unused-process-search-key",
    )
    result = read_configuration(example)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["unused-process-key", "http://127.0.0.1:19999", "unused-process-search-key"]


def test_exported_key_without_dotenv_remains_supported(example):
    example[1]["OPENAI_API_KEY"] = "unused-process-key"
    result = read_configuration(example)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["unused-process-key", "http://localhost:8080", None]


def test_missing_key_still_reports_configuration_error(example):
    result = read_configuration(example)
    assert result.returncode != 0
    assert "API key not found" in result.stderr


@pytest.mark.parametrize(
    "entry_point", [["-m", "deep_research"], ["-c", "from deep_research.main import chat_loop; chat_loop()"]]
)
def test_native_entry_points_start_with_only_dotenv(example, entry_point):
    write_dotenv(example[0])
    result = subprocess.run(
        [sys.executable, *entry_point],
        input="/exit\n",
        cwd=example[0],
        env=example[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Goodbye." in result.stdout
