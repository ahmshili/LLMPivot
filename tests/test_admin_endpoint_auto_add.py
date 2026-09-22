from __future__ import annotations

from ai_gateway.admin.routes import _auto_add_new_models_to_priority
from ai_gateway.config.manager import ConfigManager
from ai_gateway.config.models import EndpointProviderConfig, ModelEntry


def test_auto_add_appends_unknown_model_to_priority_list(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    entries = [EndpointProviderConfig(name="groq", models=["brand-new-model"])]
    _auto_add_new_models_to_priority(config, entries)

    patterns = [m if isinstance(m, str) else m.pattern for m in config.providers["groq"].defaults.models]
    assert "brand-new-model" in patterns


def test_auto_add_does_not_duplicate_model_already_in_priority_list(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = ["llama-4-scout"]

    entries = [EndpointProviderConfig(name="groq", models=["llama-4-scout"])]
    _auto_add_new_models_to_priority(config, entries)

    patterns = [m if isinstance(m, str) else m.pattern for m in config.providers["groq"].defaults.models]
    assert patterns.count("llama-4-scout") == 1


def test_auto_add_does_not_add_model_already_in_standard_list(sample_config_path, account_env_vars) -> None:
    """The model is already known/tracked in the standard list -- it
    should NOT get duplicated into priority too.
    """
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = []
    config.providers["groq"].defaults.standard_models = ["already-standard-model"]

    entries = [EndpointProviderConfig(name="groq", models=["already-standard-model"])]
    _auto_add_new_models_to_priority(config, entries)

    priority_patterns = [m if isinstance(m, str) else m.pattern for m in config.providers["groq"].defaults.models]
    assert "already-standard-model" not in priority_patterns


def test_auto_add_recognizes_model_entry_objects_not_just_bare_strings(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = [ModelEntry(pattern="llama-4-scout", comment="kept")]

    entries = [EndpointProviderConfig(name="groq", models=["llama-4-scout"])]
    _auto_add_new_models_to_priority(config, entries)

    # Still just the one entry -- not duplicated as a second, bare-string copy.
    assert len(config.providers["groq"].defaults.models) == 1


def test_auto_add_skips_entries_with_no_custom_models(sample_config_path, account_env_vars) -> None:
    """entry.models is None when the endpoint uses the provider's
    defaults rather than a custom override -- nothing to reconcile.
    """
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    original_count = len(config.providers["groq"].defaults.models)

    entries = [EndpointProviderConfig(name="groq", models=None)]
    _auto_add_new_models_to_priority(config, entries)

    assert len(config.providers["groq"].defaults.models) == original_count


def test_auto_add_skips_unknown_provider_gracefully(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    entries = [EndpointProviderConfig(name="does-not-exist", models=["some-model"])]
    # Should not raise.
    _auto_add_new_models_to_priority(config, entries)
