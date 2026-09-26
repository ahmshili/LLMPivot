"""Admin UI routes: server-rendered CRUD for providers, accounts, models,
and endpoints. Every write goes through ConfigWriter, so it's validated
against the same GatewayConfig model ConfigManager uses at startup and
backed up before it touches disk.

Unauthenticated by design -- see ai_gateway.admin package docstring.
PivotLLM -- https://github.com/ahmshili/LLMPivot -- Copyright (c) ahmshili.
Portfolio project, source-available license (see LICENSE at repo root):
view/evaluate only, no redistribution, no forks outside PRs to the
original repo, no production/commercial use without permission. This
notice must be preserved. Contact: a.shili.pers@gmail.com
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_gateway.admin.env_file import backup_env_now, remove_env_var, upsert_env_var
from ai_gateway.admin.provider_catalog import CURATED_PROVIDERS
from ai_gateway.admin.slugify import sanitize_account_key, slugify_account_label, unique_account_key
from ai_gateway.admin.testing import (
    build_test_candidate,
    ordered_test_models,
    priority_ordered_models,
    test_all_accounts,
    test_candidate_with_retry,
)
from ai_gateway.candidates import ModelValidationError, pattern_text
from ai_gateway.config.manager import default_env_var_name
from ai_gateway.config.models import (
    AccountConfig,
    EndpointConfig,
    EndpointProviderConfig,
    GatewayConfig,
    ModelEntry,
    ProviderConfig,
    ProviderDefaults,
)
from ai_gateway.cooldown import AccountState
from ai_gateway.notice import (
    AUTHOR_EMAIL,
    AUTHOR_NAME,
    GITHUB_URL,
    GITLAB_URL,
    ISSUES_URL,
    LICENSE_URL,
    PROJECT_NAME,
)
from ai_gateway.reload import reload_candidates

logger = logging.getLogger("ai_gateway.admin.routes")

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Exposed as Jinja globals (not hardcoded in the template) so the footer
# in base.html always reflects ai_gateway.notice, the single source of
# truth for author/contact/repo info. See LICENSE and notice.py: this
# attribution must be preserved in any deployment or fork-for-PR.
templates.env.globals.update(
    project_name=PROJECT_NAME,
    author_name=AUTHOR_NAME,
    author_email=AUTHOR_EMAIL,
    github_url=GITHUB_URL,
    gitlab_url=GITLAB_URL,
    issues_url=ISSUES_URL,
    license_url=LICENSE_URL,
)


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200) -> HTMLResponse:
    """Wraps Jinja2Templates.TemplateResponse for the Starlette signature
    that takes `request` as an explicit first argument.
    """
    return templates.TemplateResponse(request, name, context or {}, status_code=status_code)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _provider_summaries(config: GatewayConfig) -> list[dict]:
    return [
        {
            "name": name,
            "litellm_prefix": p.litellm_prefix or f"{name}/",
            "account_count": len(p.accounts),
            "model_count": len(p.defaults.models),
            "comment": p.comment,
        }
        for name, p in config.providers.items()
    ]


def _endpoint_summaries(config: GatewayConfig, gateway_router) -> list[dict]:
    candidates_by_endpoint = gateway_router.candidates_by_endpoint
    return [
        {
            "name": name,
            "candidate_count": len(candidates_by_endpoint.get(name, [])),
            "comment": endpoint.comment,
        }
        for name, endpoint in config.endpoints.items()
    ]


async def _provider_data(app_state, config: GatewayConfig) -> dict:
    """Embedded into the endpoint form as JSON so the browser can populate
    per-provider model/account pickers without a round trip.

    Two separate model lists, deliberately not one:
    - `priority_models`: just the provider's priority list, resolved to
      real model names (never a raw pattern string like 'gemini-2.5-*' --
      that was a real bug). This is what pre-fills the picker when you
      toggle off "use provider defaults" -- intentionally the priority
      subset only, so you start from what the provider already
      prioritizes and add more from there.
    - `models`: the full live, non-excluded catalog for that provider,
      priority-first. This is the suggestion pool for the "add a model"
      input -- deliberately broader than `priority_models`, so you can
      add models beyond the priority set.

    Accounts carry both their stable key (submitted as the form value)
    and their username (shown as the label and used for filtering).
    """
    result = {}
    for name, p in config.providers.items():
        live_models = await _live_models_for_provider(app_state, name, p)
        result[name] = {
            "priority_models": priority_ordered_models(p, live_models),
            "models": ordered_test_models(p, live_models),
            "accounts": [
                {"key": account_name, "username": (account or AccountConfig()).username or account_name}
                for account_name, account in p.accounts.items()
            ],
        }
    return result


def _state_badge_class(state: AccountState) -> str:
    return {
        AccountState.HEALTHY: "badge-ok",
        AccountState.COOLDOWN: "badge-cooldown",
        AccountState.DISABLED: "badge-disabled",
    }.get(state, "")


def _account_rows(app_state, provider_name: str, provider: ProviderConfig) -> list[dict]:
    rows = []
    for account_name, account in provider.accounts.items():
        account = account or AccountConfig()
        state = app_state.cooldown_manager.get_state(f"{provider_name}:{account_name}")
        env_var = app_state.config_manager.resolve_api_key_env(provider_name, account_name)
        rows.append(
            {
                "key": account_name,
                "username": account.username or account_name,
                "env_var": env_var,
                "api_key": os.environ.get(env_var, ""),
                "comment": account.comment,
                "state_label": state.value,
                "badge_class": _state_badge_class(state),
            }
        )
    return rows


def _model_entries_for_display(entries: list) -> list[dict]:
    result = []
    for raw_entry in entries:
        if isinstance(raw_entry, ModelEntry):
            result.append({"pattern": raw_entry.pattern, "comment": raw_entry.comment, "enabled": raw_entry.enabled})
        else:
            result.append({"pattern": raw_entry, "comment": None, "enabled": True})
    return result


async def _live_models_split_by_exclude(
    app_state, provider_name: str, provider: ProviderConfig
) -> tuple[list[str], list[str]]:
    """Concrete models LiteLLM currently reports for this provider (with
    the provider's own wildcard registration itself, e.g. bare '*' once
    the prefix is stripped, always removed -- that string is a routing
    registration, never a valid model), split into (included, excluded)
    by provider.exclude_models. Both lists are sorted.
    """
    try:
        available = await app_state.gateway_client.list_models()
    except Exception:  # noqa: BLE001 - admin display best-effort, never fatal
        logger.warning("Could not fetch live model list from LiteLLM for the admin UI.", exc_info=True)
        return [], []
    prefix = provider.litellm_prefix or f"{provider_name}/"
    stripped = (m[len(prefix):] for m in available if m.startswith(prefix))
    concrete = [m for m in stripped if m and not any(ch in m for ch in "*?[")]

    compiled = [re.compile(fnmatch.translate(p)) for p in provider.exclude_models]
    if not compiled:
        return sorted(concrete), []

    included, excluded = [], []
    for m in concrete:
        (excluded if any(rx.match(m) for rx in compiled) else included).append(m)
    return sorted(included), sorted(excluded)


async def _live_models_for_provider(app_state, provider_name: str, provider: ProviderConfig) -> list[str]:
    """Convenience wrapper for call sites that only need the included list."""
    included, _ = await _live_models_split_by_exclude(app_state, provider_name, provider)
    return included


async def _attach_live_models(context: dict, app_state, provider_name: str, provider: ProviderConfig) -> None:
    """Populates `live_models` (included, alphabetical -- datalist/reference
    list), `excluded_live_models` (alphabetical -- the new "excluded by
    your patterns" reference list), and `test_model_options` (priority-list
    matches first, in priority order, then remaining live models -- used
    for the Test dropdowns, so the default selection actually reflects the
    configured priority list instead of whichever model happens to sort
    first).
    """
    included, excluded = await _live_models_split_by_exclude(app_state, provider_name, provider)
    context["live_models"] = included
    context["excluded_live_models"] = excluded
    context["test_model_options"] = ordered_test_models(provider, included)


async def _safe_reload(request: Request) -> list[str]:
    """Recompile candidates after a config write. The write itself has
    already succeeded and is on disk either way; this only affects whether
    the change is live yet.
    """
    app_state = request.app.state
    try:
        await reload_candidates(app_state.config_manager, app_state.gateway_client, app_state.router)
        return []
    except ModelValidationError as exc:
        logger.error("Admin UI: reload after write failed strict_model_validation:\n%s", exc)
        return [f"Saved, but reload failed strict_model_validation: {exc}"]


def _provider_detail_context(request: Request, provider_name: str, provider: ProviderConfig, errors: list[str]) -> dict:
    models = _model_entries_for_display(provider.defaults.models)
    standard_models = _model_entries_for_display(provider.defaults.standard_models)
    accounts = _account_rows(request.app.state, provider_name, provider)
    return {
        "request": request,
        "provider_name": provider_name,
        "errors": errors,
        "values": {
            "litellm_prefix": provider.litellm_prefix or f"{provider_name}/",
            "comment": provider.comment or "",
            "exclude_models_text": "\n".join(provider.exclude_models),
        },
        "models": models,
        "models_json": json.dumps(models),
        "standard_models": standard_models,
        "standard_models_json": json.dumps(standard_models),
        "accounts": accounts,
        # Minimal projection (never api_key) for the JS-side per-row test
        # buttons on both model tables -- no reason for those inline
        # <script> blocks to carry anything beyond what they need.
        "accounts_json": json.dumps([{"key": a["key"], "username": a["username"]} for a in accounts]),
    }


def _parse_endpoint_form(form, config: GatewayConfig, name_override: str | None = None):
    name = (name_override or form.get("endpoint_name", "")).strip()
    comment = (form.get("comment", "") or "").strip()
    errors: list[str] = []
    if not name:
        errors.append("Endpoint name is required.")

    # Encounter order, NOT a numeric sort: browsers serialize form fields
    # in document order, so if the entry reorder buttons physically move a
    # row's DOM node, its fields genuinely submit earlier/later regardless
    # of the numeric suffix baked into their names at row-creation time.
    # Sorting by that number would silently discard every reorder action.
    indices: list[int] = []
    for k in form.keys():
        if k.startswith("provider_") and k.rsplit("_", 1)[-1].isdigit():
            idx = int(k.rsplit("_", 1)[-1])
            if idx not in indices:
                indices.append(idx)

    entries: list[EndpointProviderConfig] = []
    for i in indices:
        provider_name = (form.get(f"provider_{i}") or "").strip()
        if not provider_name:
            continue
        if provider_name not in config.providers:
            errors.append(f"Unknown provider '{provider_name}' in a provider row.")
            continue

        use_defaults = form.get(f"use_defaults_{i}") == "on"
        if use_defaults:
            models = None
        else:
            try:
                models = json.loads(form.get(f"models_json_{i}", "[]")) or []
            except (json.JSONDecodeError, TypeError):
                models = []

        use_all_accounts = form.get(f"use_all_accounts_{i}") == "on"
        accounts = None if use_all_accounts else form.getlist(f"accounts_{i}")

        include_remaining = form.get(f"include_remaining_models_{i}") == "on"

        entries.append(
            EndpointProviderConfig(
                name=provider_name,
                models=models,
                accounts=accounts,
                include_remaining_models=include_remaining,
            )
        )

    if not entries and not errors:
        errors.append("At least one provider is required.")

    return name, comment, entries, errors


def _entries_as_dicts(entries: list[EndpointProviderConfig]) -> list[dict]:
    return [
        {
            "provider": e.name,
            "models": e.models,
            "accounts": e.accounts,
            "include_remaining_models": e.include_remaining_models,
        }
        for e in entries
    ]


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = request.app.state.config_manager.config
    return render(
        request,
        "dashboard.html",
        {
            "providers": _provider_summaries(config),
            "endpoints": _endpoint_summaries(config, request.app.state.router),
            "success": request.query_params.get("success"),
        },
    )


@router.get("/export", response_class=HTMLResponse)
async def export_view(request: Request):
    """A single, copy-friendly dump of everything configured -- every
    provider's priority/standard model lists and accounts (usernames
    only, never secrets), plus every endpoint's resolved provider/model
    composition -- meant to be pasted into an LLM chat to get help
    reorganizing things.

    Format choice (this had an open design question, resolved with a
    default rather than blocking on it): a clean YAML-shaped text dump of
    this gateway's own *configured intent* (config.yaml's structure, not
    a literal dump of the file), NOT a raw copy of config.yaml -- config
    alone was explicitly noted by the user as incomplete on its own,
    since LiteLLM's model discovery doesn't expose "live" models for
    every provider (see the GitHub/etc. discovery-limitation entry in
    the "Outstanding issues" section of this doc). So each provider's
    section also includes its live models from LiteLLM, clearly labeled
    as a separate, supplementary section -- giving an LLM both the
    structured intent and the live reality in one paste.
    """
    app_state = request.app.state
    config = app_state.config_manager.config

    providers_export: dict = {}
    for name, provider in config.providers.items():
        live_models = await _live_models_for_provider(app_state, name, provider)
        providers_export[name] = {
            "comment": provider.comment,
            "litellm_prefix": provider.litellm_prefix,
            "priority_models": [_entry_as_plain(e) for e in provider.defaults.models],
            "standard_models": [_entry_as_plain(e) for e in provider.defaults.standard_models],
            "accounts": [
                ((account or AccountConfig()).username or account_name)
                for account_name, account in provider.accounts.items()
            ],
            "live_models_from_litellm": live_models,
        }

    endpoints_export: dict = {}
    for name, endpoint in config.endpoints.items():
        endpoints_export[name] = {
            "comment": endpoint.comment,
            "providers": [
                {
                    "name": entry.name,
                    "models": entry.models,
                    "accounts": entry.accounts,
                    "include_remaining_models": entry.include_remaining_models,
                }
                for entry in endpoint.providers
            ],
        }

    export_text = yaml.dump(
        {"providers": providers_export, "endpoints": endpoints_export},
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    return render(request, "export.html", {"export_text": export_text})


def _entry_as_plain(entry) -> str | dict:
    """Reduce a ModelEntry/bare-string model-list entry to plain YAML-
    friendly data for the export dump -- comments and enabled-state are
    worth keeping visible (an LLM helping reorganize models should know a
    model is disabled), a bare string stays a bare string.
    """
    if isinstance(entry, ModelEntry):
        if entry.comment or not entry.enabled:
            return {"pattern": entry.pattern, "comment": entry.comment, "enabled": entry.enabled}
        return entry.pattern
    return entry


@router.post("/backup")
async def create_backup(request: Request):
    """Manual, on-demand snapshot of both config.yaml and .env -- distinct
    from the automatic backup that already happens before every write.
    Everything this project stores (providers, endpoints, accounts, and
    the account secrets in .env) lives in exactly these two files, so
    backing both up covers all of it.
    """
    app_state = request.app.state
    app_state.config_writer.backup_now()
    backup_env_now(app_state.env_file_path)
    return RedirectResponse("/admin/?success=Backup+created.", status_code=303)


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

@router.get("/providers", response_class=HTMLResponse)
async def list_providers(request: Request):
    config = request.app.state.config_manager.config
    return render(request, "providers_list.html", {"providers": _provider_summaries(config)})


@router.get("/providers/new", response_class=HTMLResponse)
async def new_provider_form(request: Request):
    return render(
        request,
        "provider_form.html",
        {"errors": [], "values": {}, "curated_providers": CURATED_PROVIDERS},
    )


@router.post("/providers")
async def create_provider(
    request: Request, name: str = Form(...), litellm_prefix: str = Form(""), comment: str = Form("")
):
    name = name.strip()
    litellm_prefix = litellm_prefix.strip()
    errors = []
    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()

    if not name:
        errors.append("Provider name is required.")
    elif name in working.providers:
        errors.append(f"Provider '{name}' already exists.")

    if errors:
        return render(
            request,
            "provider_form.html",
            {
                "errors": errors,
                "values": {"name": name, "litellm_prefix": litellm_prefix, "comment": comment},
                "curated_providers": CURATED_PROVIDERS,
            },
            status_code=400,
        )

    working.providers[name] = ProviderConfig(litellm_prefix=litellm_prefix or None, comment=comment.strip() or None)
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{name}", status_code=303)


@router.get("/providers/{provider_name}", response_class=HTMLResponse)
async def provider_detail(request: Request, provider_name: str):
    app_state = request.app.state
    config = app_state.config_manager.config
    if provider_name not in config.providers:
        return RedirectResponse("/admin/providers", status_code=303)
    provider = config.providers[provider_name]

    context = _provider_detail_context(request, provider_name, provider, [])
    await _attach_live_models(context, app_state, provider_name, provider)
    return render(request, "provider_detail.html", context)


@router.post("/providers/{provider_name}")
async def update_provider(
    request: Request, provider_name: str, litellm_prefix: str = Form(""), comment: str = Form("")
):
    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers:
        return RedirectResponse("/admin/providers", status_code=303)

    working.providers[provider_name].litellm_prefix = litellm_prefix.strip() or None
    working.providers[provider_name].comment = comment.strip() or None
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


@router.post("/providers/{provider_name}/delete")
async def delete_provider(request: Request, provider_name: str):
    app_state = request.app.state
    config_writer = app_state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers:
        return RedirectResponse("/admin/providers", status_code=303)

    referencing_endpoints = [
        ep_name
        for ep_name, ep in working.endpoints.items()
        if any(entry.name == provider_name for entry in ep.providers)
    ]
    if referencing_endpoints:
        provider = working.providers[provider_name]
        context = _provider_detail_context(
            request,
            provider_name,
            provider,
            [
                f"Can't delete: still used by endpoint(s) {', '.join(referencing_endpoints)}. "
                "Remove it from those endpoints first."
            ],
        )
        await _attach_live_models(context, app_state, provider_name, provider)
        return render(request, "provider_detail.html", context, status_code=400)

    del working.providers[provider_name]
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse("/admin/providers", status_code=303)


def _parse_model_entries(raw_json: str) -> list[str | ModelEntry]:
    """Shared parsing for both the priority and standard model lists --
    same {pattern, comment, enabled} shape submitted by the admin UI's
    JS, same "bare string unless it actually needs the richer object
    form" collapsing.
    """
    try:
        raw_entries = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        raw_entries = []

    result: list[str | ModelEntry] = []
    for entry in raw_entries:
        pattern = (entry.get("pattern") or "").strip()
        if not pattern:
            continue
        comment = (entry.get("comment") or "").strip()
        enabled = entry.get("enabled", True) is not False
        if comment or not enabled:
            result.append(ModelEntry(pattern=pattern, comment=comment or None, enabled=enabled))
        else:
            result.append(pattern)
    return result


@router.post("/providers/{provider_name}/models")
async def save_models(
    request: Request,
    provider_name: str,
    models_json: str = Form("[]"),
    standard_models_json: str = Form("[]"),
    exclude_models: str = Form(""),
):
    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers:
        return RedirectResponse("/admin/providers", status_code=303)

    model_list = _parse_model_entries(models_json)
    standard_list = _parse_model_entries(standard_models_json)

    # Mutual exclusivity, enforced here too (not just in the UI's move
    # actions) -- a pattern in both submitted lists is a client bug or a
    # stale page, not something to silently write to config.yaml.
    # Priority wins any conflict: if a pattern is present in both, it's
    # dropped from the standard list.
    priority_patterns = {pattern_text(e) for e in model_list}
    standard_list = [e for e in standard_list if pattern_text(e) not in priority_patterns]

    exclude_list = [line.strip() for line in exclude_models.splitlines() if line.strip()]
    working.providers[provider_name].defaults = ProviderDefaults(models=model_list, standard_models=standard_list)
    working.providers[provider_name].exclude_models = exclude_list

    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

@router.post("/providers/{provider_name}/accounts")
async def add_account(
    request: Request,
    provider_name: str,
    username: str = Form(...),
    api_key: str = Form(...),
    label: str = Form(""),
    env_var_override: str = Form(""),
    comment: str = Form(""),
):
    app_state = request.app.state
    config_writer = app_state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers:
        return RedirectResponse("/admin/providers", status_code=303)

    username = username.strip()
    api_key = api_key.strip()
    label = label.strip()
    env_var_override = env_var_override.strip()
    errors = []
    if not username:
        errors.append("Username is required.")
    if not api_key:
        errors.append("API key is required.")

    if errors:
        provider = working.providers[provider_name]
        context = _provider_detail_context(request, provider_name, provider, errors)
        await _attach_live_models(context, app_state, provider_name, provider)
        return render(request, "provider_detail.html", context, status_code=400)

    # An explicit label overrides the auto-derived slug entirely (no
    # domain-stripping -- the admin typed this on purpose, e.g. 'personal'
    # instead of the auto-generated 'jane_doe'). Falls back to
    # deriving from the username exactly as before when left blank.
    base_slug = sanitize_account_key(label) if label else slugify_account_label(username)
    account_key = unique_account_key(working.providers[provider_name].accounts, base_slug)
    env_var = env_var_override or default_env_var_name(provider_name, account_key)

    working.providers[provider_name].accounts[account_key] = AccountConfig(
        username=username, api_key_env=env_var, comment=comment.strip() or None
    )
    config_writer.write(working)

    upsert_env_var(app_state.env_file_path, env_var, api_key)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


@router.get("/providers/{provider_name}/accounts/{account_name}/edit", response_class=HTMLResponse)
async def edit_account_form(request: Request, provider_name: str, account_name: str):
    config = request.app.state.config_manager.config
    if provider_name not in config.providers or account_name not in config.providers[provider_name].accounts:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)
    provider = config.providers[provider_name]
    account = provider.accounts[account_name] or AccountConfig()
    env_var = request.app.state.config_manager.resolve_api_key_env(provider_name, account_name)
    return render(
        request,
        "account_edit.html",
        {
            "provider_name": provider_name,
            "account_name": account_name,
            "errors": [],
            "values": {
                "username": account.username or account_name,
                "comment": account.comment or "",
            },
            "env_var": env_var,
            "current_api_key": os.environ.get(env_var, ""),
        },
    )


@router.post("/providers/{provider_name}/accounts/{account_name}/edit")
async def edit_account(
    request: Request, provider_name: str, account_name: str, username: str = Form(...), comment: str = Form("")
):
    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers or account_name not in working.providers[provider_name].accounts:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    username = username.strip()
    if not username:
        return render(
            request,
            "account_edit.html",
            {
                "provider_name": provider_name,
                "account_name": account_name,
                "errors": ["Username is required."],
                "values": {"username": username, "comment": comment},
                "env_var": request.app.state.config_manager.resolve_api_key_env(provider_name, account_name),
            },
            status_code=400,
        )

    account = working.providers[provider_name].accounts[account_name] or AccountConfig()
    account.username = username
    account.comment = comment.strip() or None
    working.providers[provider_name].accounts[account_name] = account
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


@router.post("/providers/{provider_name}/accounts/{account_name}/rotate-key")
async def rotate_key(request: Request, provider_name: str, account_name: str, api_key: str = Form(...)):
    app_state = request.app.state
    config = app_state.config_manager.config
    if provider_name not in config.providers or account_name not in config.providers[provider_name].accounts:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    api_key = api_key.strip()
    if api_key:
        env_var = app_state.config_manager.resolve_api_key_env(provider_name, account_name)
        upsert_env_var(app_state.env_file_path, env_var, api_key)
    return RedirectResponse(f"/admin/providers/{provider_name}/accounts/{account_name}/edit", status_code=303)


@router.post("/providers/{provider_name}/accounts/{account_name}/rename-env-var")
async def rename_env_var(request: Request, provider_name: str, account_name: str, new_env_var: str = Form(...)):
    app_state = request.app.state
    config_writer = app_state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers or account_name not in working.providers[provider_name].accounts:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    new_env_var = new_env_var.strip()
    if not new_env_var:
        return RedirectResponse(f"/admin/providers/{provider_name}/accounts/{account_name}/edit", status_code=303)

    old_env_var = app_state.config_manager.resolve_api_key_env(provider_name, account_name)
    old_value = os.environ.get(old_env_var, "")

    account = working.providers[provider_name].accounts[account_name] or AccountConfig()
    account.api_key_env = new_env_var
    working.providers[provider_name].accounts[account_name] = account
    config_writer.write(working)

    if old_value:
        # Copy the existing secret to the new variable name so the
        # account keeps working immediately; the old line is left in
        # place, same "orphaned lines are harmless" policy as deletes.
        upsert_env_var(app_state.env_file_path, new_env_var, old_value)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}/accounts/{account_name}/edit", status_code=303)


@router.post("/providers/{provider_name}/accounts/{account_name}/rename-key")
async def rename_account_key(request: Request, provider_name: str, account_name: str, new_key: str = Form(...)):
    """Renames the account's internal key (the config.yaml dict key / URL
    segment / cooldown-state key) -- separate from username, which is the
    freely-editable display name. Any endpoint explicitly referencing the
    old key in its accounts list is updated to the new key, not stripped,
    since the account still exists, just under a different name.
    """
    app_state = request.app.state
    config_writer = app_state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers or account_name not in working.providers[provider_name].accounts:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    new_key = sanitize_account_key(new_key.strip()) if new_key.strip() else account_name
    if new_key == account_name:
        return RedirectResponse(f"/admin/providers/{provider_name}/accounts/{account_name}/edit", status_code=303)

    accounts = working.providers[provider_name].accounts
    if new_key in accounts:
        provider = working.providers[provider_name]
        context = _provider_detail_context(
            request, provider_name, provider, [f"Account key '{new_key}' is already in use for this provider."]
        )
        await _attach_live_models(context, app_state, provider_name, provider)
        return render(request, "provider_detail.html", context, status_code=400)

    # Rebuild the dict with the renamed key in the same position, then
    # update any endpoint entries that explicitly reference the old key.
    working.providers[provider_name].accounts = {
        (new_key if k == account_name else k): v for k, v in accounts.items()
    }
    for ep in working.endpoints.values():
        for entry in ep.providers:
            if entry.name == provider_name and entry.accounts and account_name in entry.accounts:
                entry.accounts = [new_key if a == account_name else a for a in entry.accounts]

    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}/accounts/{new_key}/edit", status_code=303)


@router.post("/providers/{provider_name}/accounts/{account_name}/delete")
async def delete_account(request: Request, provider_name: str, account_name: str):
    app_state = request.app.state
    config_writer = app_state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers or account_name not in working.providers[provider_name].accounts:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    # Resolve the env var name while the account still exists in config --
    # once it's deleted, resolve_api_key_env has nothing to look up.
    env_var = app_state.config_manager.resolve_api_key_env(provider_name, account_name)

    del working.providers[provider_name].accounts[account_name]

    # Deleting an account must not leave a dangling reference in any
    # endpoint's explicit accounts list -- that would fail validation on
    # the very next write. Strip it out (falling back to "all accounts"
    # for that provider entry if the list becomes empty) instead of
    # blocking the delete.
    cleaned_endpoints = []
    for ep_name, ep in working.endpoints.items():
        for entry in ep.providers:
            if entry.name == provider_name and entry.accounts and account_name in entry.accounts:
                entry.accounts = [a for a in entry.accounts if a != account_name] or None
                cleaned_endpoints.append(ep_name)

    config_writer.write(working)
    remove_env_var(app_state.env_file_path, env_var)
    await _safe_reload(request)
    if cleaned_endpoints:
        logger.info(
            "Removed deleted account '%s:%s' from endpoint(s): %s",
            provider_name,
            account_name,
            ", ".join(cleaned_endpoints),
        )
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


@router.post("/providers/{provider_name}/accounts/delete-bulk")
async def delete_accounts_bulk(request: Request, provider_name: str, keys: str = Form(...)):
    """Bulk variant of delete_account, for the admin UI's multi-select
    "remove selected" bulk action -- deletes several accounts in one
    write+reload instead of one round trip per account. Same per-account
    cleanup as the single-delete route above: removes each account's .env
    line, strips any dangling endpoint account references, and gets the
    same automatic config.yaml/.env backup as every other config_writer
    write.
    """
    app_state = request.app.state
    config_writer = app_state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    try:
        requested_keys = json.loads(keys)
    except (json.JSONDecodeError, TypeError):
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)
    if not isinstance(requested_keys, list):
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    accounts = working.providers[provider_name].accounts
    to_delete = [k for k in requested_keys if isinstance(k, str) and k in accounts]
    if not to_delete:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    # Resolve each env var name while the account still exists in config --
    # once deleted, resolve_api_key_env has nothing left to look up.
    env_vars_to_remove = [
        app_state.config_manager.resolve_api_key_env(provider_name, account_name) for account_name in to_delete
    ]
    for account_name in to_delete:
        del working.providers[provider_name].accounts[account_name]

    cleaned_endpoints = []
    for ep_name, ep in working.endpoints.items():
        for entry in ep.providers:
            if entry.name == provider_name and entry.accounts:
                remaining = [a for a in entry.accounts if a not in to_delete]
                if remaining != entry.accounts:
                    entry.accounts = remaining or None
                    cleaned_endpoints.append(ep_name)

    config_writer.write(working)
    for env_var in env_vars_to_remove:
        remove_env_var(app_state.env_file_path, env_var)
    await _safe_reload(request)
    if cleaned_endpoints:
        logger.info(
            "Removed deleted account(s) %s from endpoint(s): %s",
            ", ".join(to_delete),
            ", ".join(cleaned_endpoints),
        )
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


@router.post("/providers/{provider_name}/accounts/{account_name}/move")
async def move_account(request: Request, provider_name: str, account_name: str, direction: str = Form(...)):
    """Reorders accounts within a provider. Python dicts preserve
    insertion order, so this rebuilds provider.accounts with the account
    moved to its new position -- the only way to change order for a
    dict-shaped config section.
    """
    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers or account_name not in working.providers[provider_name].accounts:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    accounts = working.providers[provider_name].accounts
    keys = list(accounts.keys())
    i = keys.index(account_name)

    if direction == "up" and i > 0:
        keys[i - 1], keys[i] = keys[i], keys[i - 1]
    elif direction == "down" and i < len(keys) - 1:
        keys[i + 1], keys[i] = keys[i], keys[i + 1]
    elif direction == "top" and i > 0:
        keys.insert(0, keys.pop(i))
    elif direction == "bottom" and i < len(keys) - 1:
        keys.append(keys.pop(i))
    else:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    working.providers[provider_name].accounts = {k: accounts[k] for k in keys}
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


@router.post("/providers/{provider_name}/accounts/reorder")
async def reorder_accounts(request: Request, provider_name: str, order: str = Form(...)):
    """Bulk reorder for the admin UI's drag-and-drop / multi-select move
    controls -- takes the full new account-key order in one request
    (JSON-encoded list of keys) rather than one call per single-step move.

    Kept as a separate route from move_account rather than folded into
    it, so the existing single-direction fallback buttons (still present
    for no-JS/no-drag use) keep working completely unchanged.

    Same dict-rebuild-then-write pattern as move_account: Python dicts
    preserve insertion order, so provider.accounts is rebuilt keyed in
    the requested order, then written and reloaded exactly the same way.
    """
    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    if provider_name not in working.providers:
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    accounts = working.providers[provider_name].accounts
    try:
        requested_order = json.loads(order)
    except (json.JSONDecodeError, TypeError):
        return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)

    # Defensive: only accept a permutation of the account keys that
    # actually exist today. Anything requested that isn't a real key is
    # dropped silently; any real key missing from the request keeps its
    # relative position appended at the end, so a stale/partial payload
    # (e.g. a race with a concurrent edit) can't silently delete accounts.
    existing_keys = list(accounts.keys())
    new_order = [k for k in requested_order if isinstance(k, str) and k in accounts]
    new_order += [k for k in existing_keys if k not in new_order]

    working.providers[provider_name].accounts = {k: accounts[k] for k in new_order}
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse(f"/admin/providers/{provider_name}", status_code=303)


@router.post("/providers/{provider_name}/accounts/{account_name}/test", response_class=HTMLResponse)
async def test_account(request: Request, provider_name: str, account_name: str, model: str = Form(...)):
    app_state = request.app.state
    config = app_state.config_manager.config
    candidate = build_test_candidate(config, app_state.config_manager, provider_name, account_name, model)
    outcome = await test_candidate_with_retry(
        app_state.gateway_client, candidate, config.admin.test_retry_delay_seconds
    )
    return render(request, "partials/test_result.html", {"outcome": outcome})


@router.post("/providers/{provider_name}/accounts/test-all", response_class=HTMLResponse)
async def test_all(request: Request, provider_name: str):
    app_state = request.app.state
    config = app_state.config_manager.config
    if provider_name not in config.providers:
        return HTMLResponse("Unknown provider.", status_code=404)
    provider = config.providers[provider_name]
    live_models = await _live_models_for_provider(app_state, provider_name, provider)
    results = await test_all_accounts(
        app_state.gateway_client, config, app_state.config_manager, provider_name, live_models, config.admin
    )
    return render(request, "partials/batch_test_results.html", {"results": results})


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@router.get("/endpoints", response_class=HTMLResponse)
async def list_endpoints(request: Request):
    config = request.app.state.config_manager.config
    return render(request, "endpoints_list.html", {"endpoints": _endpoint_summaries(config, request.app.state.router)})


@router.get("/endpoints/new", response_class=HTMLResponse)
async def new_endpoint_form(request: Request):
    config = request.app.state.config_manager.config
    provider_data = await _provider_data(request.app.state, config)
    return render(
        request,
        "endpoint_form.html",
        {
            "editing": False,
            "endpoint_name": None,
            "comment": "",
            "errors": [],
            "provider_names": list(config.providers),
            "provider_data_json": json.dumps(provider_data),
            "existing_entries_json": json.dumps([]),
        },
    )


@router.get("/endpoints/{endpoint_name}/edit", response_class=HTMLResponse)
async def edit_endpoint_form(request: Request, endpoint_name: str):
    config = request.app.state.config_manager.config
    if endpoint_name not in config.endpoints:
        return RedirectResponse("/admin/endpoints", status_code=303)
    endpoint = config.endpoints[endpoint_name]
    provider_data = await _provider_data(request.app.state, config)
    return render(
        request,
        "endpoint_form.html",
        {
            "editing": True,
            "endpoint_name": endpoint_name,
            "comment": endpoint.comment or "",
            "errors": [],
            "provider_names": list(config.providers),
            "provider_data_json": json.dumps(provider_data),
            "existing_entries_json": json.dumps(_entries_as_dicts(endpoint.providers)),
        },
    )


def _auto_add_new_models_to_priority(working: GatewayConfig, entries: list) -> None:
    """When an endpoint's custom model list includes a model that isn't
    already in that provider's priority list *or* standard list, add it
    to the priority list -- keeps the provider's own config as the
    source of truth for every model actually in use somewhere, rather
    than letting a model exist only inside one endpoint's override with
    no provider-level representation at all. Confirmed behavior: always
    the priority list, never the standard list, even though the model
    wasn't necessarily meant to be high-priority -- an operator can
    always move it to standard afterward if that's not what they wanted.

    Mutates `working` in place; call before config_writer.write(working)
    so this lands in the same atomic write as the endpoint itself.
    """
    for entry in entries:
        if not entry.models:
            continue
        provider = working.providers.get(entry.name)
        if provider is None:
            continue
        known_patterns = {pattern_text(e) for e in provider.defaults.models} | {
            pattern_text(e) for e in provider.defaults.standard_models
        }
        for model in entry.models:
            if model not in known_patterns:
                provider.defaults.models.append(model)
                known_patterns.add(model)


@router.post("/endpoints")
async def create_endpoint(request: Request):
    form = await request.form()
    config = request.app.state.config_manager.config
    name, comment, entries, errors = _parse_endpoint_form(form, config)

    if not errors and name in config.endpoints:
        errors.append(f"Endpoint '{name}' already exists.")

    if errors:
        provider_data = await _provider_data(request.app.state, config)
        return render(
            request,
            "endpoint_form.html",
            {
                "editing": False,
                "endpoint_name": name,
                "comment": comment,
                "errors": errors,
                "provider_names": list(config.providers),
                "provider_data_json": json.dumps(provider_data),
                "existing_entries_json": json.dumps(_entries_as_dicts(entries)),
            },
            status_code=400,
        )

    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    working.endpoints[name] = EndpointConfig(providers=entries, comment=comment or None)
    _auto_add_new_models_to_priority(working, entries)
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse("/admin/endpoints", status_code=303)


@router.post("/endpoints/{endpoint_name}")
async def update_endpoint(request: Request, endpoint_name: str):
    form = await request.form()
    config = request.app.state.config_manager.config
    _, comment, entries, errors = _parse_endpoint_form(form, config, name_override=endpoint_name)

    if errors:
        provider_data = await _provider_data(request.app.state, config)
        return render(
            request,
            "endpoint_form.html",
            {
                "editing": True,
                "endpoint_name": endpoint_name,
                "comment": comment,
                "errors": errors,
                "provider_names": list(config.providers),
                "provider_data_json": json.dumps(provider_data),
                "existing_entries_json": json.dumps(_entries_as_dicts(entries)),
            },
            status_code=400,
        )

    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    if endpoint_name not in working.endpoints:
        return RedirectResponse("/admin/endpoints", status_code=303)
    working.endpoints[endpoint_name] = EndpointConfig(providers=entries, comment=comment or None)
    _auto_add_new_models_to_priority(working, entries)
    config_writer.write(working)
    await _safe_reload(request)
    return RedirectResponse("/admin/endpoints", status_code=303)


@router.post("/endpoints/{endpoint_name}/delete")
async def delete_endpoint(request: Request, endpoint_name: str):
    config_writer = request.app.state.config_writer
    working = config_writer.working_copy()
    if endpoint_name in working.endpoints:
        del working.endpoints[endpoint_name]
        config_writer.write(working)
        await _safe_reload(request)
    return RedirectResponse("/admin/endpoints", status_code=303)
