"""ConfigWriter: the only component that writes config.yaml on the admin
UI's behalf.

Every write goes: take a working copy -> mutate it in memory -> validate
the WHOLE object with the exact same GatewayConfig Pydantic model
ConfigManager already uses at startup -> only on success, back up the
current file and overwrite it -> reload ConfigManager's in-memory config
from the new file, so it stays the single source of truth.

Nothing is ever hand-templated as YAML text, so a malformed config.yaml
should be structurally impossible to produce through this path.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ai_gateway.config.manager import ConfigManager
from ai_gateway.config.models import GatewayConfig

logger = logging.getLogger("ai_gateway.admin.config_writer")


class ConfigWriter:
    def __init__(self, config_manager: ConfigManager) -> None:
        self._config_manager = config_manager

    def working_copy(self) -> GatewayConfig:
        """A deep-copied, mutable GatewayConfig safe to modify without
        affecting the live config until write() is called.
        """
        return self._config_manager.config.model_copy(deep=True)

    def backup_now(self) -> None:
        """Manual, on-demand backup with no accompanying write -- for an
        explicit "back everything up now" action, distinct from the
        automatic backup that happens before every write.
        """
        self._backup(self._config_manager.config_path)

    def write(self, config: GatewayConfig) -> GatewayConfig:
        """Validate the whole config object, back up the current file,
        then overwrite it, then reload ConfigManager from the new file.
        """
        # Round-trip through model_dump -> model_validate rather than
        # trusting the in-memory object directly: this re-runs every
        # validator (including the endpoint/provider/account referential
        # checks) exactly as ConfigManager.load() would on a fresh read.
        validated = GatewayConfig.model_validate(config.model_dump(mode="json"))
        path = self._config_manager.config_path
        self._backup(path)
        path.write_text(
            yaml.safe_dump(
                # exclude_defaults (not just exclude_none) so fields like
                # include_remaining_models=False, left at its default,
                # don't clutter every entry in config.yaml -- only
                # explicitly-set-away-from-default values are written.
                validated.model_dump(mode="json", exclude_defaults=True),
                sort_keys=False,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )
        logger.info("Wrote %s via admin UI.", path)
        return self._config_manager.load()

    @staticmethod
    def _backup(path: Path) -> None:
        if not path.exists():
            return
        # Microsecond precision deliberately: rapid successive writes
        # (e.g. several admin UI actions in quick succession) would
        # otherwise collide on the same second-resolution filename and
        # silently overwrite each other's backup.
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")
        backup_path = path.with_name(f"{path.name}.{timestamp}.bak")
        shutil.copy2(path, backup_path)
        logger.info("Backed up %s to %s before write.", path, backup_path)
