"""Parsing of the model's structured output, with graceful degradation.

The happy path is a strict `LlmResponse.model_validate_json`. When the model
returns something slightly off — fenced JSON, a stray prose preamble, one bad
action in an otherwise good response, or plain prose with no JSON at all — we
salvage as much as we can rather than discarding the whole turn. A malformed
action is dropped (never guessed at), because executing a misread trade is far
worse than skipping it.
"""

import json
import logging

from pydantic import ValidationError

from .models import LlmResponse, TradeAction, WatchlistChange

logger = logging.getLogger(__name__)


class LlmParseError(Exception):
    """The response could not be salvaged into anything useful."""


def _strip_fences(text: str) -> str:
    """Remove a leading ```json fence and its closing fence, if present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    closing = body.rfind("```")
    if closing != -1:
        body = body[:closing]
    return body.strip()


def _extract_json_object(text: str) -> str | None:
    """Return the outermost balanced {...} span, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _salvage(payload: dict) -> LlmResponse:
    """Rebuild a response from a dict, discarding individually invalid actions."""
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise LlmParseError("response has no usable 'message' field")

    trades = []
    for raw in payload.get("trades") or []:
        try:
            trades.append(TradeAction.model_validate(raw))
        except (ValidationError, TypeError):
            logger.warning("Discarding malformed trade action from LLM response")

    changes = []
    for raw in payload.get("watchlist_changes") or []:
        try:
            changes.append(WatchlistChange.model_validate(raw))
        except (ValidationError, TypeError):
            logger.warning("Discarding malformed watchlist change from LLM response")

    return LlmResponse(message=message.strip(), trades=trades, watchlist_changes=changes)


def parse_llm_response(content: str | None) -> LlmResponse:
    """Parse raw model output into an LlmResponse.

    Raises LlmParseError only when nothing at all can be recovered.
    """
    if not content or not content.strip():
        raise LlmParseError("empty response from model")

    cleaned = _strip_fences(content)

    # Strict path.
    try:
        return LlmResponse.model_validate_json(cleaned)
    except ValidationError:
        pass

    # Salvage path: find the JSON object and rebuild it field by field.
    candidate = _extract_json_object(cleaned)
    if candidate is not None:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            logger.warning("LLM response required salvage parsing")
            return _salvage(payload)

    # Last resort: the model answered in prose. Show it, execute nothing.
    logger.warning("LLM response was not JSON; treating it as plain prose")
    return LlmResponse(message=cleaned.strip())
