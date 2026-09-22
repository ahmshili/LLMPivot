from __future__ import annotations

from pathlib import Path

from ai_gateway.main import _resolve_data_paths


def test_defaults_when_nothing_set(monkeypatch) -> None:
    monkeypatch.delenv("AI_GATEWAY_DATA_DIR", raising=False)
    monkeypatch.delenv("AI_GATEWAY_CONFIG", raising=False)
    monkeypatch.delenv("AI_GATEWAY_ENV_FILE", raising=False)
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("config.yaml")
    assert env_path == Path(".env")


def test_data_dir_sets_both_paths(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "/tmp/some-data-dir")
    monkeypatch.delenv("AI_GATEWAY_CONFIG", raising=False)
    monkeypatch.delenv("AI_GATEWAY_ENV_FILE", raising=False)
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("/tmp/some-data-dir/config.yaml")
    assert env_path == Path("/tmp/some-data-dir/.env")


def test_explicit_config_overrides_data_dir(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "/tmp/some-data-dir")
    monkeypatch.setenv("AI_GATEWAY_CONFIG", "/tmp/explicit-config.yaml")
    monkeypatch.delenv("AI_GATEWAY_ENV_FILE", raising=False)
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("/tmp/explicit-config.yaml")
    assert env_path == Path("/tmp/some-data-dir/.env")


def test_explicit_env_file_overrides_data_dir(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_DATA_DIR", "/tmp/some-data-dir")
    monkeypatch.delenv("AI_GATEWAY_CONFIG", raising=False)
    monkeypatch.setenv("AI_GATEWAY_ENV_FILE", "/tmp/explicit.env")
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("/tmp/some-data-dir/config.yaml")
    assert env_path == Path("/tmp/explicit.env")


def test_no_data_dir_uses_plain_env_vars(monkeypatch) -> None:
    monkeypatch.delenv("AI_GATEWAY_DATA_DIR", raising=False)
    monkeypatch.setenv("AI_GATEWAY_CONFIG", "custom-config.yaml")
    monkeypatch.setenv("AI_GATEWAY_ENV_FILE", "custom.env")
    config_path, env_path = _resolve_data_paths()
    assert config_path == Path("custom-config.yaml")
    assert env_path == Path("custom.env")
