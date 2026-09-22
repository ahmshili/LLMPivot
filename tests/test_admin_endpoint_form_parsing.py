from __future__ import annotations

import pytest

from ai_gateway.admin.routes import (
    _entries_as_dicts,
    _live_models_split_by_exclude,
    _parse_endpoint_form,
    _provider_data,
)
from ai_gateway.config.manager import ConfigManager


class FakeFormData(dict):
    """Minimal stand-in for Starlette's FormData: dict.keys() preserves
    insertion order, exactly like a real submitted form does (browsers
    serialize fields in document order).
    """

    def getlist(self, key):
        value = self.get(key)
        return [value] if value is not None else []


def test_parse_endpoint_form_basic(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    form = FakeFormData(
        {
            "endpoint_name": "planner",
            "comment": "test endpoint",
            "provider_0": "gemini",
            "use_defaults_0": "on",
            "use_all_accounts_0": "on",
        }
    )
    name, comment, entries, errors = _parse_endpoint_form(form, config)
    assert name == "planner"
    assert comment == "test endpoint"
    assert errors == []
    assert len(entries) == 1
    assert entries[0].name == "gemini"
    assert entries[0].models is None
    assert entries[0].include_remaining_models is False


def test_parse_endpoint_form_uses_submission_order_not_numeric_index(
    sample_config_path, account_env_vars
) -> None:
    # This is the critical fix: field names carry a numeric suffix
    # assigned at row-CREATION time, but the entry reorder buttons move a
    # row's DOM node, which changes the ORDER fields are submitted in --
    # not their numeric suffix. If parsing sorted numerically instead of
    # respecting encounter order, every reorder action would silently be
    # discarded. Simulate a reordered submission: row "1" (groq) appears
    # before row "0" (gemini) in the form body, because the user moved it
    # to the top.
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    form = FakeFormData()
    form["endpoint_name"] = "planner"
    form["provider_1"] = "groq"  # submitted first despite higher numeric suffix
    form["use_defaults_1"] = "on"
    form["use_all_accounts_1"] = "on"
    form["provider_0"] = "gemini"  # submitted second despite lower numeric suffix
    form["use_defaults_0"] = "on"
    form["use_all_accounts_0"] = "on"

    name, comment, entries, errors = _parse_endpoint_form(form, config)
    assert errors == []
    assert [e.name for e in entries] == ["groq", "gemini"], (
        "entries must follow submission/DOM order, not the numeric suffix in field names"
    )


def test_parse_endpoint_form_explicit_models_json(sample_config_path, account_env_vars) -> None:
    import json

    manager = ConfigManager(sample_config_path)
    config = manager.load()

    form = FakeFormData(
        {
            "endpoint_name": "planner",
            "provider_0": "gemini",
            # use_defaults_0 intentionally omitted -> unchecked -> custom list
            "models_json_0": json.dumps(["gemini-2.5-pro", "gemini-2.5-flash"]),
            "use_all_accounts_0": "on",
        }
    )
    _, _, entries, errors = _parse_endpoint_form(form, config)
    assert errors == []
    assert entries[0].models == ["gemini-2.5-pro", "gemini-2.5-flash"]


def test_parse_endpoint_form_include_remaining_models_flag(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    form = FakeFormData(
        {
            "endpoint_name": "planner",
            "provider_0": "gemini",
            "use_defaults_0": "on",
            "include_remaining_models_0": "on",
            "use_all_accounts_0": "on",
        }
    )
    _, _, entries, errors = _parse_endpoint_form(form, config)
    assert errors == []
    assert entries[0].include_remaining_models is True


def test_parse_endpoint_form_unknown_provider_errors(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    form = FakeFormData(
        {
            "endpoint_name": "planner",
            "provider_0": "not-a-real-provider",
            "use_defaults_0": "on",
            "use_all_accounts_0": "on",
        }
    )
    _, _, entries, errors = _parse_endpoint_form(form, config)
    assert entries == []
    assert any("not-a-real-provider" in e for e in errors)


def test_entries_as_dicts_round_trip_includes_new_field(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    dicts = _entries_as_dicts(config.endpoints["planner"].providers)
    assert all("include_remaining_models" in d for d in dicts)


def test_all_expected_admin_routes_are_registered() -> None:
    """Guards against a route silently losing its @router decorator (which
    happened once during development -- a str_replace edit swallowed the
    decorator for test_account, and no pytest test caught it because none
    exercised that exact path; only an ad-hoc smoke script did). This
    checks presence/method for every route the admin UI depends on.
    """
    from ai_gateway.admin.routes import router

    registered = {(frozenset(r.methods), r.path) for r in router.routes}

    expected = [
        ({"GET"}, "/admin/"),
        ({"GET"}, "/admin/export"),
        ({"GET"}, "/admin/providers"),
        ({"GET"}, "/admin/providers/new"),
        ({"POST"}, "/admin/providers"),
        ({"GET"}, "/admin/providers/{provider_name}"),
        ({"POST"}, "/admin/providers/{provider_name}"),
        ({"POST"}, "/admin/providers/{provider_name}/delete"),
        ({"POST"}, "/admin/providers/{provider_name}/models"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts"),
        ({"GET"}, "/admin/providers/{provider_name}/accounts/{account_name}/edit"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/{account_name}/edit"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/{account_name}/rotate-key"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/{account_name}/rename-env-var"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/{account_name}/rename-key"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/{account_name}/move"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/reorder"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/{account_name}/delete"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/delete-bulk"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/{account_name}/test"),
        ({"POST"}, "/admin/providers/{provider_name}/accounts/test-all"),
        ({"GET"}, "/admin/endpoints"),
        ({"GET"}, "/admin/endpoints/new"),
        ({"GET"}, "/admin/endpoints/{endpoint_name}/edit"),
        ({"POST"}, "/admin/endpoints"),
        ({"POST"}, "/admin/endpoints/{endpoint_name}"),
        ({"POST"}, "/admin/endpoints/{endpoint_name}/delete"),
        ({"POST"}, "/admin/backup"),
    ]
    missing = [(methods, path) for methods, path in expected if (frozenset(methods), path) not in registered]
    assert not missing, f"Missing route registrations: {missing}"


class _FakeGatewayClientForData:
    def __init__(self, models):
        self.models = models

    async def list_models(self):
        return self.models


class _FakeAppState:
    def __init__(self, models):
        self.gateway_client = _FakeGatewayClientForData(models)


import pytest


@pytest.mark.asyncio
async def test_live_models_split_by_exclude_separates_correctly(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].exclude_models = ["*-tts", "*-image*"]

    app_state = _FakeAppState(
        [
            "gemini/gemini-2.5-flash",
            "gemini/gemini-2.5-flash-preview-tts",
            "gemini/gemini-2.5-flash-image",
        ]
    )
    included, excluded = await _live_models_split_by_exclude(app_state, "gemini", config.providers["gemini"])
    assert included == ["gemini-2.5-flash"]
    assert set(excluded) == {"gemini-2.5-flash-preview-tts", "gemini-2.5-flash-image"}


@pytest.mark.asyncio
async def test_live_models_split_by_exclude_no_patterns_returns_empty_excluded(
    sample_config_path, account_env_vars
) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()

    app_state = _FakeAppState(["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro"])
    included, excluded = await _live_models_split_by_exclude(app_state, "gemini", config.providers["gemini"])
    assert set(included) == {"gemini-2.5-flash", "gemini-2.5-pro"}
    assert excluded == []


@pytest.mark.asyncio
async def test_provider_data_seeds_models_from_live_models_not_raw_patterns(
    sample_config_path, account_env_vars
) -> None:
    # This is the confirmed bug fix: the endpoint picker used to seed from
    # provider.defaults.models verbatim (raw pattern strings like
    # 'gemini-2.5-*'), which could leave an unexpanded wildcard sitting in
    # the picker as if it were a selectable model.
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].defaults.models = ["gemini-2.5-*"]

    app_state = _FakeAppState(
        ["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro", "gemini/gemini-1.5-flash", "groq/llama-4-scout"]
    )
    data = await _provider_data(app_state, config)

    assert "gemini-2.5-*" not in data["gemini"]["models"], "raw wildcard pattern must not appear in seed data"
    assert set(data["gemini"]["models"]) == {"gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash"}
    # Priority matches (gemini-2.5-*) should sort before the non-priority model.
    assert data["gemini"]["models"].index("gemini-1.5-flash") > min(
        data["gemini"]["models"].index("gemini-2.5-flash"), data["gemini"]["models"].index("gemini-2.5-pro")
    )


@pytest.mark.asyncio
async def test_provider_data_excludes_filtered_models_from_seed(sample_config_path, account_env_vars) -> None:
    manager = ConfigManager(sample_config_path)
    config = manager.load()
    config.providers["gemini"].defaults.models = ["gemini-2.5-*"]
    config.providers["gemini"].exclude_models = ["*-tts"]

    app_state = _FakeAppState(["gemini/gemini-2.5-flash", "gemini/gemini-2.5-flash-preview-tts"])
    data = await _provider_data(app_state, config)
    assert "gemini-2.5-flash-preview-tts" not in data["gemini"]["models"]
    assert "gemini-2.5-flash" in data["gemini"]["models"]
