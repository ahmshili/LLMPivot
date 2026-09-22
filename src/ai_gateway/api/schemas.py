"""Minimal OpenAI-compatible request/response schemas.

These are intentionally loose: the gateway forwards most fields straight
through to LiteLLM untouched (temperature, max_tokens, etc.), so we only
model the fields the gateway itself needs to inspect or reject.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatCompletionRequest(BaseModel):
    """Client-facing request body for /v1/chat/completions.

    Extra fields are allowed and passed through verbatim to LiteLLM, since
    this gateway does not know (nor want to know) every parameter each
    provider might support.
    """

    model_config = ConfigDict(extra="allow")

    model: str = Field(..., description="Virtual endpoint name, e.g. 'planner'.")
    messages: list[dict[str, Any]]
    stream: bool = False


class ModelListEntry(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "ai-gateway"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelListEntry]


class ErrorDetail(BaseModel):
    message: str
    type: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
