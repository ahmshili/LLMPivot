from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ai_gateway.admin.config_writer import ConfigWriter
from ai_gateway.admin.env_file import remove_env_var, upsert_env_var
from ai_gateway.config.manager import ConfigManager
from ai_gateway.config.models import EndpointConfig, EndpointProviderConfig, ProviderConfig


def test_config_writer_round_trip(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()
    writer = ConfigWriter(manager)

    working = writer.working_copy()
    working.providers["groq"].accounts["secondary"] = None
    result = writer.write(working)

    assert "secondary" in result.providers["groq"].accounts
    # ConfigManager's in-memory config was reloaded from disk too.
    assert "secondary" in manager.config.providers["groq"].accounts


def test_config_writer_creates_backup_before_write(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()
    writer = ConfigWriter(manager)

    working = writer.working_copy()
    writer.write(working)

    backups = list(sample_config_path.parent.glob(f"{sample_config_path.name}.*.bak"))
    assert len(backups) == 1


def test_config_writer_rejects_invalid_endpoint_reference(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()
    writer = ConfigWriter(manager)

    working = writer.working_copy()
    working.endpoints["broken"] = EndpointConfig(
        providers=[EndpointProviderConfig(name="does-not-exist")]
    )
    with pytest.raises(Exception):
        writer.write(working)

    # The bad write must not have reached disk.
    on_disk = yaml.safe_load(sample_config_path.read_text())
    assert "broken" not in on_disk.get("endpoints", {})


def test_config_writer_written_yaml_is_loadable(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()
    writer = ConfigWriter(manager)

    working = writer.working_copy()
    working.providers["new_provider"] = ProviderConfig(litellm_prefix="new_provider/")
    writer.write(working)

    reloaded = ConfigManager(sample_config_path)
    config = reloaded.load()
    assert "new_provider" in config.providers


def test_upsert_env_var_appends_new_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=1\n")

    upsert_env_var(env_path, "NEW_KEY", "value1")

    content = env_path.read_text()
    assert "EXISTING=1" in content
    assert "NEW_KEY=value1" in content
    import os

    assert os.environ["NEW_KEY"] == "value1"


def test_upsert_env_var_replaces_existing_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("MY_KEY=old\nOTHER=2\n")

    upsert_env_var(env_path, "MY_KEY", "new")

    lines = env_path.read_text().splitlines()
    assert "MY_KEY=new" in lines
    assert "MY_KEY=old" not in lines
    assert "OTHER=2" in lines


def test_upsert_env_var_creates_file_if_missing(tmp_path: Path) -> None:
    env_path = tmp_path / "nested" / ".env"
    upsert_env_var(env_path, "K", "v")
    assert env_path.read_text().strip() == "K=v"


def test_remove_env_var_deletes_line_and_unsets_process_env(tmp_path: Path) -> None:
    import os

    env_path = tmp_path / ".env"
    env_path.write_text("KEEP_ME=1\nDELETE_ME=secret\n")
    os.environ["DELETE_ME"] = "secret"

    remove_env_var(env_path, "DELETE_ME")

    content = env_path.read_text()
    assert "DELETE_ME" not in content
    assert "KEEP_ME=1" in content
    assert "DELETE_ME" not in os.environ


def test_remove_env_var_missing_key_is_a_noop(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("SOMETHING=1\n")
    remove_env_var(env_path, "NOT_PRESENT")  # must not raise
    assert env_path.read_text() == "SOMETHING=1\n"


def test_upsert_env_var_creates_backup_of_existing_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=1\n")

    upsert_env_var(env_path, "EXISTING", "2")

    backups = list(tmp_path.glob(".env.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "EXISTING=1\n"  # backup holds the PRE-write content


def test_upsert_env_var_no_backup_when_file_did_not_exist(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    upsert_env_var(env_path, "NEW", "value")
    assert list(tmp_path.glob(".env.*.bak")) == []


def test_remove_env_var_creates_backup_only_when_something_actually_removed(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("KEEP=1\nGONE=2\n")

    remove_env_var(env_path, "NOT_PRESENT")
    assert list(tmp_path.glob(".env.*.bak")) == [], "no-op removal should not create a backup"

    remove_env_var(env_path, "GONE")
    backups = list(tmp_path.glob(".env.*.bak"))
    assert len(backups) == 1
    assert "GONE=2" in backups[0].read_text()
