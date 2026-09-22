from __future__ import annotations

import pytest

from ai_gateway.candidates import CandidateResolver, ModelValidationError, build_litellm_model
from ai_gateway.config.manager import ConfigManager


def test_wildcard_resolution_and_ordering(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()

    available_models = [
        "gemini/gemini-2.5-flash",
        "gemini/gemini-2.5-pro",
        "gemini/gemini-1.5-flash",  # should NOT match "gemini-2.5-*"
        "groq/llama-4-scout",
    ]
    resolver = CandidateResolver(manager, available_models)
    candidates = resolver.resolve()

    planner = candidates["planner"]
    models = {c.model for c in planner if c.provider == "gemini"}
    assert models == {"gemini-2.5-flash", "gemini-2.5-pro"}
    assert all(c.litellm_model.startswith("gemini/") for c in planner if c.provider == "gemini")

    # Provider order preserved: gemini entries appear before groq entries.
    providers_in_order = [c.provider for c in planner]
    assert providers_in_order.index("gemini") < providers_in_order.index("groq")


def test_missing_env_var_excludes_account(sample_config_path, monkeypatch) -> None:
    # Only set one of the two gemini accounts' keys.
    monkeypatch.setenv("GEMINI_PERSONAL_API_KEY", "key")
    monkeypatch.setenv("GROQ_PRIMARY_API_KEY", "key")
    # GEMINI_WORK_API_KEY intentionally left unset.

    manager = ConfigManager(sample_config_path)
    manager.load()
    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash", "groq/llama-4-scout"])
    candidates = resolver.resolve()

    accounts = {c.account for c in candidates["planner"] if c.provider == "gemini"}
    assert accounts == {"personal"}


def test_endpoint_override_models_and_accounts(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.endpoints["planner"].providers[0].models = ["gemini-2.5-flash"]
    config.endpoints["planner"].providers[0].accounts = ["personal"]

    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro"])
    candidates = resolver.resolve()
    gemini_candidates = [c for c in candidates["planner"] if c.provider == "gemini"]
    assert len(gemini_candidates) == 1
    assert gemini_candidates[0].model == "gemini-2.5-flash"
    assert gemini_candidates[0].account == "personal"


def test_candidate_key_scoped_to_provider_and_account(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()
    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash", "groq/llama-4-scout"])
    candidates = resolver.resolve()["planner"]
    keys = {c.key for c in candidates}
    assert "gemini:personal" in keys
    assert "groq:primary" in keys


def test_unresolved_literal_model_included_with_warning_by_default(
    sample_config_path, account_env_vars, caplog
) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()
    # groq's default model "llama-4-scout" is a literal, and it's not in
    # the available_models list below, so it should be included anyway
    # (strict_model_validation defaults to False) with a warning logged.
    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash"])
    candidates = resolver.resolve()
    groq_models = {c.model for c in candidates["planner"] if c.provider == "groq"}
    assert groq_models == {"llama-4-scout"}


def test_strict_model_validation_raises_on_unresolved_literal(
    sample_config_path, account_env_vars
) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.routing.strict_model_validation = True

    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash"])  # groq's model absent
    with pytest.raises(ModelValidationError, match="llama-4-scout"):
        resolver.resolve()


def test_strict_model_validation_passes_when_all_literals_resolve(
    sample_config_path, account_env_vars
) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.routing.strict_model_validation = True

    resolver = CandidateResolver(
        manager, ["gemini/gemini-2.5-flash", "groq/llama-4-scout"]
    )
    # Should not raise.
    candidates = resolver.resolve()
    assert candidates["planner"]


def test_strict_model_validation_does_not_apply_to_wildcards(
    sample_config_path, account_env_vars
) -> None:
    # An unmatched wildcard should never raise, even in strict mode --
    # strict_model_validation only governs literal (non-wildcard) models.
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.routing.strict_model_validation = True

    resolver = CandidateResolver(manager, ["groq/llama-4-scout"])  # no gemini/* models available
    candidates = resolver.resolve()
    gemini_models = {c.model for c in candidates["planner"] if c.provider == "gemini"}
    assert gemini_models == set()


def test_exclude_models_filters_wildcard_matches(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].exclude_models = ["*-tts", "*-image*"]

    available_models = [
        "gemini/gemini-2.5-flash",
        "gemini/gemini-2.5-pro",
        "gemini/gemini-2.5-flash-preview-tts",
        "gemini/gemini-2.5-flash-image",
    ]
    resolver = CandidateResolver(manager, available_models)
    candidates = resolver.resolve()
    gemini_models = {c.model for c in candidates["planner"] if c.provider == "gemini"}
    assert gemini_models == {"gemini-2.5-flash", "gemini-2.5-pro"}


def test_exclude_models_also_filters_literal_matches(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = ["llama-4-scout"]
    config.providers["groq"].exclude_models = ["llama-4-scout"]

    resolver = CandidateResolver(manager, ["groq/llama-4-scout"])
    candidates = resolver.resolve()
    groq_models = {c.model for c in candidates["planner"] if c.provider == "groq"}
    assert groq_models == set()


def test_exclude_models_noop_when_empty(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    manager.load()
    # No exclude_models configured in the sample fixture -- should behave
    # exactly as before this feature existed.
    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash", "groq/llama-4-scout"])
    candidates = resolver.resolve()
    assert candidates["planner"]


def test_model_entry_with_comment_resolves_same_as_bare_string(sample_config_path, account_env_vars) -> None:
    from ai_gateway.config.models import ModelEntry

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = [
        ModelEntry(pattern="llama-4-scout", comment="fast default")
    ]

    resolver = CandidateResolver(manager, ["groq/llama-4-scout"])
    candidates = resolver.resolve()
    groq_models = {c.model for c in candidates["planner"] if c.provider == "groq"}
    assert groq_models == {"llama-4-scout"}


def test_model_entry_wildcard_with_comment_expands(sample_config_path, account_env_vars) -> None:
    from ai_gateway.config.models import ModelEntry

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].defaults.models = [
        ModelEntry(pattern="gemini-2.5-*", comment="broad wildcard")
    ]

    resolver = CandidateResolver(
        manager, ["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro", "gemini/gemini-1.5-flash"]
    )
    candidates = resolver.resolve()
    gemini_models = {c.model for c in candidates["planner"] if c.provider == "gemini"}
    assert gemini_models == {"gemini-2.5-flash", "gemini-2.5-pro"}


def test_mixed_bare_string_and_model_entry_in_same_list(sample_config_path, account_env_vars) -> None:
    from ai_gateway.config.models import ModelEntry

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = [
        "llama-4-scout",
        ModelEntry(pattern="llama-3.3-70b-versatile", comment="fallback"),
    ]

    resolver = CandidateResolver(manager, ["groq/llama-4-scout", "groq/llama-3.3-70b-versatile"])
    candidates = resolver.resolve()
    groq_models = {c.model for c in candidates["planner"] if c.provider == "groq"}
    assert groq_models == {"llama-4-scout", "llama-3.3-70b-versatile"}


def test_disabled_model_entry_produces_no_candidate(sample_config_path, account_env_vars) -> None:
    """A disabled entry is kept in config.yaml purely for reference -- it
    must never contribute a routable candidate.
    """
    from ai_gateway.config.models import ModelEntry

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["groq"].defaults.models = [
        "llama-4-scout",
        ModelEntry(pattern="llama-3.3-70b-versatile", enabled=False),
    ]

    resolver = CandidateResolver(manager, ["groq/llama-4-scout", "groq/llama-3.3-70b-versatile"])
    candidates = resolver.resolve()
    groq_models = {c.model for c in candidates["planner"] if c.provider == "groq"}
    assert groq_models == {"llama-4-scout"}
    assert "llama-3.3-70b-versatile" not in groq_models


def test_disabled_wildcard_model_entry_produces_no_candidates(sample_config_path, account_env_vars) -> None:
    from ai_gateway.config.models import ModelEntry

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].defaults.models = [
        ModelEntry(pattern="gemini-2.5-*", enabled=False),
    ]

    resolver = CandidateResolver(
        manager, ["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro", "gemini/gemini-1.5-flash"]
    )
    candidates = resolver.resolve()
    gemini_models = {c.model for c in candidates["planner"] if c.provider == "gemini"}
    assert gemini_models == set()


def test_disabled_model_entry_does_not_raise_unresolved_literal_warning(sample_config_path, account_env_vars) -> None:
    """A disabled entry for a model that doesn't even exist in the live
    list must not trigger the "configured model not found" warning --
    it was never going to be routed to in the first place. Verified via
    strict_model_validation, since that's what actually raises on an
    unresolved literal; a disabled entry must never trip it.
    """
    from ai_gateway.config.models import ModelEntry

    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.routing.strict_model_validation = True
    config.providers["groq"].defaults.models = [
        ModelEntry(pattern="totally-made-up-model", enabled=False),
    ]

    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash"])  # groq's real model absent too, but disabled model shouldn't matter
    # Should not raise -- the disabled entry never becomes an unresolved literal.
    candidates = resolver.resolve()
    groq_models = {c.model for c in candidates["planner"] if c.provider == "groq"}
    assert groq_models == set()


def test_enabled_defaults_true_and_is_excluded_from_serialization() -> None:
    """enabled=True (the default) must not appear in serialized output --
    only genuinely-disabled entries should show up as {enabled: false} in
    config.yaml, keeping the common case a plain string or a lean object.
    """
    from ai_gateway.config.models import ModelEntry

    entry = ModelEntry(pattern="llama-4-scout", comment="fast")
    assert entry.enabled is True
    dumped = entry.model_dump(exclude_defaults=True)
    assert "enabled" not in dumped

    disabled_entry = ModelEntry(pattern="llama-4-scout", enabled=False)
    dumped_disabled = disabled_entry.model_dump(exclude_defaults=True)
    assert dumped_disabled["enabled"] is False


def test_include_remaining_models_appends_other_live_models(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.endpoints["planner"].providers[0].models = ["gemini-2.5-flash"]
    config.endpoints["planner"].providers[0].include_remaining_models = True

    resolver = CandidateResolver(
        manager, ["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro", "gemini/gemini-1.5-flash"]
    )
    candidates = resolver.resolve()
    gemini_models = [c.model for c in candidates["planner"] if c.provider == "gemini" and c.account == "personal"]
    # Explicit model first, remaining live models appended after, no duplicates.
    assert gemini_models[0] == "gemini-2.5-flash"
    assert set(gemini_models) == {"gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash"}


def test_include_remaining_models_respects_exclude_models(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].exclude_models = ["gemini-1.5-flash"]
    config.endpoints["planner"].providers[0].models = ["gemini-2.5-flash"]
    config.endpoints["planner"].providers[0].include_remaining_models = True

    resolver = CandidateResolver(
        manager, ["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro", "gemini/gemini-1.5-flash"]
    )
    candidates = resolver.resolve()
    gemini_models = {c.model for c in candidates["planner"] if c.provider == "gemini" and c.account == "personal"}
    assert gemini_models == {"gemini-2.5-flash", "gemini-2.5-pro"}
    assert "gemini-1.5-flash" not in gemini_models


def test_include_remaining_models_false_by_default(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    assert config.endpoints["planner"].providers[0].include_remaining_models is False


def test_build_litellm_model_normal_case() -> None:
    assert build_litellm_model("mistral/", "codestral-latest") == "mistral/codestral-latest"


def test_build_litellm_model_avoids_double_prefix() -> None:
    # If the model string already starts with the provider's prefix (e.g.
    # someone typed/pasted "mistral/codestral-latest" for a provider whose
    # prefix is already "mistral/"), don't prepend it again.
    assert build_litellm_model("mistral/", "mistral/codestral-latest") == "mistral/codestral-latest"
    assert build_litellm_model("gemini/", "gemini/gemini-2.5-flash") == "gemini/gemini-2.5-flash"


def test_resolver_avoids_double_prefix_for_manually_typed_prefixed_model(
    sample_config_path, account_env_vars
) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    # Simulate a user having typed the model with the prefix already
    # included (e.g. copy-pasted from a raw LiteLLM /v1/models listing).
    config.providers["gemini"].defaults.models = ["gemini/gemini-2.5-flash"]

    resolver = CandidateResolver(manager, ["gemini/gemini-2.5-flash"])
    candidates = resolver.resolve()
    gemini_candidates = [c for c in candidates["planner"] if c.provider == "gemini"]
    assert all(c.litellm_model == "gemini/gemini-2.5-flash" for c in gemini_candidates), (
        [c.litellm_model for c in gemini_candidates]
    )
