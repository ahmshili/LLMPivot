from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

SAMPLE_CONFIG: dict = {
    "litellm": {"base_url": "http://localhost:4000", "timeout_seconds": 10},
    "routing": {"max_attempts": 4, "request_timeout_seconds": 10},
    "cooldown": {
        "base_seconds": 1,
        "multiplier": 2,
        "max_seconds": 60,
        "quota_cooldown_seconds": 30,
        "unknown_failure_cooldown_seconds": 5,
    },
    "providers": {
        "gemini": {
            "litellm_prefix": "gemini/",
            "defaults": {"models": ["gemini-2.5-*"]},
            "accounts": {"personal": None, "work": None},
        },
        "groq": {
            "litellm_prefix": "groq/",
            "defaults": {"models": ["llama-4-scout"]},
            "accounts": {"primary": None},
        },
    },
    "endpoints": {
        "planner": {
            "providers": [
                {"name": "gemini"},
                {"name": "groq"},
            ]
        }
    },
}


@pytest.fixture
def sample_config_dict() -> dict:
    return SAMPLE_CONFIG


@pytest.fixture
def sample_config_path(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(SAMPLE_CONFIG), encoding="utf-8")
    return config_path


@pytest.fixture
def account_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_PERSONAL_API_KEY", "test-personal-key")
    monkeypatch.setenv("GEMINI_WORK_API_KEY", "test-work-key")
    monkeypatch.setenv("GROQ_PRIMARY_API_KEY", "test-groq-key")
