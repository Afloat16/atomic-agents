"""Validate the documented environment-file setup at example configuration boundaries."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "directory,package,code",
    [
        ("basic-multimodal", "basic_multimodal", "from basic_multimodal.main import API_KEY; print(API_KEY)"),
        ("rag-chatbot", "rag_chatbot", "from rag_chatbot.config import ChatConfig; print(ChatConfig.api_key)"),
        ("fastapi-memory", "fastapi_memory", "from fastapi_memory.lib.config import get_api_key; print(get_api_key())"),
        (
            "orchestration-agent",
            "orchestration_agent",
            "from orchestration_agent.orchestrator import orchestrator_agent; print(orchestrator_agent.client.client.api_key)",
        ),
        (
            "youtube-summarizer",
            "youtube_summarizer",
            "from youtube_summarizer.agent import youtube_knowledge_extraction_agent as agent; print(agent.client.client.api_key)",
        ),
    ],
)
@pytest.mark.parametrize("exported", [False, True])
def test_dotenv_is_loaded_before_native_configuration(tmp_path, directory, package, code, exported):
    repository = Path(__file__).resolve().parents[2]
    shutil.copytree(
        repository / "atomic-examples" / directory / package,
        tmp_path / package,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (tmp_path / ".env").write_text("OPENAI_API_KEY=unused-dotenv-key\n", encoding="utf-8")
    environment = dict(os.environ)
    for name in (
        "OPENAI_API_KEY",
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
        PYTHONPATH=str(repository / "atomic-agents"),
        LITELLM_LOCAL_MODEL_COST_MAP="True",
        PYTHONIOENCODING="utf-8",
    )
    if exported:
        environment["OPENAI_API_KEY"] = "unused-exported-key"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ("unused-exported-key" if exported else "unused-dotenv-key")
