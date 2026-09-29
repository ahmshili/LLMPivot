"""Root route: send visitors to the admin UI when it is reachable.

Kept in its own module (rather than inline in main.py) so it can be tested
without spinning up the full lifespan.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ai_gateway.notice import PROJECT_NAME

router = APIRouter()


@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root(request: Request):
    """GET / -> /admin/ when the admin UI is reachable.

    In prod mode without --enable-admin the admin routes 404 by design, so
    redirecting there would just hand visitors a 404. Return a small JSON
    pointer to the API docs instead. HEAD is handled too, since PaaS
    health probes commonly use it.
    """
    if getattr(request.app.state, "admin_available", True):
        return RedirectResponse("/admin/", status_code=302)
    return JSONResponse({"name": PROJECT_NAME, "docs": "/docs", "health": "/health"})
