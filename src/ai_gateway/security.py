"""Prod-mode authentication dependencies for /v1/* and /admin/* routes.

Both are no-ops unless prod mode is explicitly on (--prod at startup) --
local/trusted-network usage is completely unaffected by this module
existing. See SecurityConfig's docstring in config/models.py for the
full design rationale.

State this module reads from `app.state` (set once in main.py's
lifespan, never touched per-request):
    prod_mode: bool
    admin_enabled: bool
    api_auth_token: str | None
    admin_auth_token: str | None
PivotLLM -- https://github.com/ahmshili/LLMPivot -- Copyright (c) ahmshili.
Portfolio project, source-available license (see LICENSE at repo root):
view/evaluate only, no redistribution, no forks outside PRs to the
original repo, no production/commercial use without permission. This
notice must be preserved. Contact: a.shili.pers@gmail.com
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": {"message": message, "type": "authentication_error"}},
    )


def _constant_time_eq(a: str, b: str) -> bool:
    """Plain `==` on secrets is a timing side-channel in principle: an
    attacker who can measure response time precisely could learn how
    many leading characters matched. `hmac.compare_digest` is the
    standard fix -- cheap, no reason not to use it for a token compare.
    """
    import hmac

    return hmac.compare_digest(a, b)


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    return authorization[len(prefix) :]


async def require_api_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    """Applied to every /v1/* route. No-op unless prod mode is on AND an
    api_auth_token was actually configured -- an operator who runs --prod
    without configuring security.api_auth_token_env has made an explicit
    choice to leave /v1/* open (e.g. it's already behind a private
    network), not accidentally left it insecure.
    """
    if not getattr(request.app.state, "prod_mode", False):
        return
    token = getattr(request.app.state, "api_auth_token", None)
    if not token:
        return

    provided = _extract_bearer_token(authorization)
    if not provided or not _constant_time_eq(provided, token):
        raise _unauthorized("Invalid or missing API token.")


async def require_admin_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
    """Applied to every /admin/* route. Three states:

    - Not prod mode: no-op, exactly today's behavior (unauthenticated,
      reachable) -- prod mode changes nothing for local/trusted-network use.
    - Prod mode, admin not explicitly re-enabled (--enable-admin not
      passed): every admin route returns 404, as if it doesn't exist at
      all. Deliberately 404, not 401/403 -- a public scanner shouldn't be
      able to tell there's an admin UI here to even attack.
    - Prod mode, admin explicitly re-enabled: requires a valid bearer
      token matching admin_auth_token. main.py's startup validation
      guarantees this token is always actually set in this state --
      --enable-admin without a configured token fails startup entirely,
      it never reaches a state where this check would be silently
      unenforceable.
    """
    if not getattr(request.app.state, "prod_mode", False):
        return

    if not getattr(request.app.state, "admin_enabled", False):
        raise HTTPException(status_code=404)

    token = getattr(request.app.state, "admin_auth_token", None)
    provided = _extract_bearer_token(authorization)
    if not token or not provided or not _constant_time_eq(provided, token):
        raise _unauthorized("Invalid or missing admin token.")
