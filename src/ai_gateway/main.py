"""Application entrypoint.

Wires together ConfigManager -> GatewayClient -> CandidateResolver ->
CooldownManager -> FailureClassifier -> Router -> FastAPI app.

Run with:
    uv run uvicorn ai_gateway.main:app --host 0.0.0.0 --port 8000

or, for a friendlier CLI (including --data-dir), via the console script:
    uv run ai-gateway --data-dir ../data --port 8000

Configuration file path is read from the AI_GATEWAY_CONFIG environment
variable, defaulting to "config.yaml" in the current directory -- unless
AI_GATEWAY_DATA_DIR is set, in which case config.yaml and .env both
default to living inside that directory instead (so the project's data
can live outside the project checkout entirely, e.g. a sibling folder
shared with your LiteLLM deployment). Either AI_GATEWAY_CONFIG or
AI_GATEWAY_ENV_FILE, if explicitly set, always wins over the data-dir
default.
PivotLLM -- https://github.com/ahmshili/LLMPivot -- Copyright (c) ahmshili.
Portfolio project, source-available license (see LICENSE at repo root):
view/evaluate only, no redistribution, no forks outside PRs to the
original repo, no production/commercial use without permission. This
notice must be preserved. Contact: a.shili.pers@gmail.com
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request

from ai_gateway.admin.config_writer import ConfigWriter
from ai_gateway.admin.routes import router as admin_router
from ai_gateway.api import router as api_router
from ai_gateway.candidates import CandidateResolver, ModelValidationError
from ai_gateway.config.manager import ConfigManager
from ai_gateway.cooldown import CooldownManager
from ai_gateway.failure import FailureClassifier
from ai_gateway.gateway_client import GatewayClient
from ai_gateway.logging_config import configure_logging
from ai_gateway.notice import (
    AUTHOR_EMAIL,
    GITHUB_URL,
    GITLAB_URL,
    HTTP_HEADER_NAME,
    HTTP_HEADER_VALUE,
    ISSUES_URL,
    PROJECT_NAME,
)
from ai_gateway.router import Router
from ai_gateway.security import require_admin_auth

logger = logging.getLogger("ai_gateway.main")


def _resolve_data_paths() -> tuple[Path, Path]:
    """Returns (config_path, env_file_path), honoring AI_GATEWAY_DATA_DIR
    as a base directory when AI_GATEWAY_CONFIG / AI_GATEWAY_ENV_FILE
    aren't explicitly set.

    Logs the resolved *absolute* path of each, plus which precedence rule
    produced it -- this used to be silent, which made a real, confusing
    bug possible: if AI_GATEWAY_CONFIG happens to already be set in the
    shell (e.g. left over from an earlier debugging session), it silently
    wins over --data-dir/AI_GATEWAY_DATA_DIR with no visible sign that
    this happened, so config.yaml appears to load from the wrong place
    (or not at all) for a reason that isn't visible anywhere in the logs.
    """
    data_dir = os.environ.get("AI_GATEWAY_DATA_DIR")
    config_env_override = "AI_GATEWAY_CONFIG" in os.environ
    env_file_override = "AI_GATEWAY_ENV_FILE" in os.environ

    if data_dir:
        base = Path(data_dir)
        config_path = Path(os.environ["AI_GATEWAY_CONFIG"]) if config_env_override else base / "config.yaml"
        env_path = Path(os.environ["AI_GATEWAY_ENV_FILE"]) if env_file_override else base / ".env"
    else:
        config_path = Path(os.environ.get("AI_GATEWAY_CONFIG", "config.yaml"))
        env_path = Path(os.environ.get("AI_GATEWAY_ENV_FILE", ".env"))

    config_source = "AI_GATEWAY_CONFIG (explicit override)" if config_env_override else (
        "AI_GATEWAY_DATA_DIR" if data_dir else "default (current directory)"
    )
    env_source = "AI_GATEWAY_ENV_FILE (explicit override)" if env_file_override else (
        "AI_GATEWAY_DATA_DIR" if data_dir else "default (current directory)"
    )
    logger.info(
        "Resolved config path: %s (source: %s); env file path: %s (source: %s).",
        config_path.resolve(),
        config_source,
        env_path.resolve(),
        env_source,
    )
    return config_path, env_path


class StartupConfigError(Exception):
    """Raised for a prod-mode misconfiguration severe enough to refuse
    startup entirely, rather than starting in a silently-insecure state.
    """


def _resolve_security_state(security_config) -> tuple[bool, bool, str | None, str | None]:
    """Returns (prod_mode, admin_enabled, api_auth_token, admin_auth_token).

    prod_mode and admin_enabled come from CLI flags (--prod,
    --enable-admin), surfaced as env vars by cli.py the same way every
    other flag in this project is -- see AI_GATEWAY_DATA_DIR etc. above.
    The tokens themselves are resolved from security_config's env-var
    *names*, never read directly from config.yaml.

    Hard-fails startup if --enable-admin was passed without a real,
    resolvable admin token -- there's no safe "intentionally left open"
    reading for the admin UI the way there is for the API token, given
    what it exposes (provider accounts, API keys, full config control).
    """
    prod_mode = os.environ.get("AI_GATEWAY_PROD_MODE", "").lower() in ("1", "true", "yes")
    admin_enabled_flag = os.environ.get("AI_GATEWAY_ENABLE_ADMIN", "").lower() in ("1", "true", "yes")

    api_auth_token = None
    if security_config.api_auth_token_env:
        api_auth_token = os.environ.get(security_config.api_auth_token_env) or None

    admin_auth_token = None
    if security_config.admin_auth_token_env:
        admin_auth_token = os.environ.get(security_config.admin_auth_token_env) or None

    admin_enabled = prod_mode and admin_enabled_flag

    if prod_mode and admin_enabled_flag and not admin_auth_token:
        raise StartupConfigError(
            "--enable-admin was passed (with --prod) but no usable admin "
            "auth token was resolved. Set security.admin_auth_token_env "
            "in config.yaml to name an environment variable, and set that "
            "variable in .env to a real value. Refusing to start rather "
            "than expose the admin UI (provider accounts, API keys, full "
            "config control) without authentication."
        )

    if prod_mode and not api_auth_token:
        logger.warning(
            "Prod mode is on, but no api_auth_token is configured (security.api_auth_token_env). "
            "/v1/* routes will remain unauthenticated. If this is unintentional, set "
            "security.api_auth_token_env in config.yaml and the matching value in .env."
        )

    if prod_mode and admin_enabled:
        logger.warning(
            "Admin UI is enabled in prod mode (--enable-admin). It requires the configured "
            "admin bearer token on every request. Make sure this deployment is otherwise secured "
            "(HTTPS at minimum) before relying on this."
        )
    elif prod_mode:
        logger.info("Prod mode is on: admin UI is disabled (pass --enable-admin to re-enable it).")

    return prod_mode, admin_enabled, api_auth_token, admin_auth_token


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(os.environ.get("AI_GATEWAY_LOG_LEVEL", "INFO"))

    config_path, env_file_path = _resolve_data_paths()
    config_manager = ConfigManager(config_path)
    config = config_manager.load()

    gateway_client = GatewayClient(config.litellm)
    available_models = await gateway_client.list_models()
    logger.info("LiteLLM reports %d model(s) available.", len(available_models))

    resolver = CandidateResolver(config_manager, available_models)
    try:
        candidates_by_endpoint = resolver.resolve()
    except ModelValidationError as exc:
        logger.error("Startup aborted due to strict_model_validation failure:\n%s", exc)
        raise

    cooldown_manager = CooldownManager(config.cooldown)
    failure_classifier = FailureClassifier()
    gateway_router = Router(
        candidates_by_endpoint=candidates_by_endpoint,
        cooldown_manager=cooldown_manager,
        failure_classifier=failure_classifier,
        gateway_client=gateway_client,
        routing_config=config.routing,
    )

    app.state.config_manager = config_manager
    app.state.gateway_client = gateway_client
    app.state.cooldown_manager = cooldown_manager
    app.state.failure_classifier = failure_classifier
    app.state.router = gateway_router
    # Admin UI: the only consumer of these two. ConfigWriter is the sole
    # writer of config.yaml outside of manual editing; env_file_path tells
    # the admin UI which dotenv file to upsert secrets into (it has no
    # other way to know, since `--env-file` is consumed by the launcher,
    # not this process).
    app.state.config_writer = ConfigWriter(config_manager)
    app.state.env_file_path = env_file_path

    prod_mode, admin_enabled, api_auth_token, admin_auth_token = _resolve_security_state(config.security)
    app.state.prod_mode = prod_mode
    app.state.admin_enabled = admin_enabled
    app.state.api_auth_token = api_auth_token
    app.state.admin_auth_token = admin_auth_token

    logger.info("LLMPivot started with %d endpoint(s).", len(candidates_by_endpoint))
    try:
        yield
    finally:
        await gateway_client.aclose()
        logger.info("LLMPivot shut down.")


app = FastAPI(
    title=f"{PROJECT_NAME} (formerly LLMPivot)",
    description=(
        "A lightweight OpenAI-compatible gateway that orchestrates free-tier LLM "
        "APIs on top of LiteLLM.\n\n"
        f"Author: {PROJECT_NAME.lower()} devs (contact: {AUTHOR_EMAIL}) · "
        f"Source: {GITHUB_URL} · Mirror: {GITLAB_URL} · Issues: {ISSUES_URL}\n\n"
        "Portfolio project distributed under a source-available, "
        "view-and-evaluate license -- not open source. Redistribution, "
        "forking outside pull requests to the original repository, and "
        "production/commercial deployment are not permitted without the "
        "author's written consent. See the LICENSE file in the repository "
        "above for full terms."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def _attribution_header(request: Request, call_next):
    """Stamps every response with the project's canonical source location.

    This is attribution, not access control -- it never blocks or alters a
    response, it only labels it. See NOTICE / LICENSE for why this header
    (and its source in notice.py) must be preserved.
    """
    response = await call_next(request)
    response.headers[HTTP_HEADER_NAME] = HTTP_HEADER_VALUE
    return response


# api_router's auth is applied per-route (see api/routes.py), not here at
# the whole-router level -- /health must stay reachable unauthenticated
# for PaaS health checks even when prod mode is on, so a single uniform
# router-wide dependency would be wrong here, unlike admin_router below
# where every route should be uniformly gated.
app.include_router(api_router)
app.include_router(admin_router, dependencies=[Depends(require_admin_auth)])
