from __future__ import annotations

from pathlib import Path

import pytest

from ai_gateway.main import _resolve_data_paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("AI_GATEWAY_DATA_DIR", "AI_GATEWAY_CONFIG", "AI_GATEWAY_ENV_FILE"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_cwd_when_nothing_set() -> None:
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("config.yaml")
    assert env_path == Path(".env")


def test_data_dir_sets_both_defaults(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "../data")
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("../data/config.yaml")
    assert env_path == Path("../data/.env")


def test_explicit_config_wins_over_data_dir(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "../data")
    monkeypatch.setenv("AI_GATEWAY_CONFIG", "/somewhere/else/config.yaml")
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("/somewhere/else/config.yaml")
    assert env_path == Path("../data/.env")  # env file still follows data_dir


def test_explicit_env_file_wins_over_data_dir(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "../data")
    monkeypatch.setenv("AI_GATEWAY_ENV_FILE", "/somewhere/else/.env")
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("../data/config.yaml")
    assert env_path == Path("/somewhere/else/.env")


def test_explicit_config_and_env_without_data_dir(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_CONFIG", "custom.yaml")
    monkeypatch.setenv("AI_GATEWAY_ENV_FILE", "custom.env")
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("custom.yaml")
    assert env_path == Path("custom.env")


def test_logs_resolved_paths_and_source(monkeypatch, caplog) -> None:
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "../data")
    with caplog.at_level("INFO", logger="ai_gateway.main"):
        _resolve_data_paths()
    assert "AI_GATEWAY_DATA_DIR" in caplog.text
    assert "config.yaml" in caplog.text


def test_logs_explicit_override_source_when_config_env_wins(monkeypatch, caplog) -> None:
    """Regression test for a real reported bug: a person passed --data-dir
    but a stale AI_GATEWAY_CONFIG left over from an earlier session
    silently won instead, with the app falling back to a bare config.yaml
    relative to the current directory instead of the data dir. The fix
    isn't to change the (deliberate, documented) precedence -- it's to
    make it visible when this happens, so it's diagnosable in one log
    line instead of requiring a failed startup and manual deduction.
    """
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "../data")
    monkeypatch.setenv("AI_GATEWAY_CONFIG", "config.yaml")
    with caplog.at_level("INFO", logger="ai_gateway.main"):
        config_path, _ = _resolve_data_paths()
    assert config_path == Path("config.yaml")
    assert "explicit override" in caplog.text
