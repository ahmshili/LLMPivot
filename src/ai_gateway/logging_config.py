"""Logging setup.

Every routing decision (candidate selected, failure type, cooldown,
retry) is logged by Router and CooldownManager at INFO/WARNING level via
the standard library logging module. This module configures a readable,
consistent format for all of it -- including uvicorn's own loggers
(access, error/startup, and the reloader), which otherwise format their
lines differently (no timestamp, different prefix) and visually fragment
the console output.

Human-readability features (colors, highlights, blank-line grouping) are
strictly TTY-only: piped/redirected output is plain text so grep, log
shippers, and CI never see ANSI escapes. NO_COLOR disables colors even
on a TTY (https://no-color.org/); FORCE_COLOR forces them when piping.
"""

from __future__ import annotations

import logging
import os
import re
import sys

# Loggers uvicorn installs its own handlers on. Left alone, their lines
# ("INFO:     127.0.0.1:x - \"POST ...\" 200 OK") have no timestamp and a
# different prefix than every ai_gateway line, so a single request reads
# as two unrelated log formats interleaved. Re-handlering them with the
# shared formatter makes every console line uniform.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# ANSI SGR codes -- plain strings, no colorama dependency.
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

_LEVEL_COLORS = {
    logging.DEBUG: _CYAN,
    logging.INFO: _GREEN,
    logging.WARNING: _YELLOW,
    logging.ERROR: _RED,
    logging.CRITICAL: _RED + _BOLD,
}

# Phrase highlights for known log lines (kept as literal phrases so the
# formatter stays decoupled from the emitters' message templates).
_HIGHLIGHT_COLORS = {
    "Test OK": _GREEN + _BOLD,
    "Test FAILED": _RED + _BOLD,
    "retrying once": _YELLOW + _BOLD,
}

# The [provider/account] tag test lines now lead with: bold cyan, so the
# identity of the tested account reads at column 0 of every event line.
# Content is deliberately permissive (slashes/colons included): the retry
# tag embeds the model too, e.g. [groq/primary/qwen/qwen3.6-27b].
_ACCOUNT_TAG_PATTERN = re.compile(r"(?P<tag>\[[^\[\]]+\])")

# Model names in test lines: quoted 'model' mentions, bold white so they
# pop against the dim logger name and normal surrounding text.
_MODEL_PATTERN = re.compile(r"(?P<full>model\s+'[^']+')")

# Failure badge + status on outcome lines: "AUTH_FAILURE (403)",
# "TRANSIENT (503)" -- bold red, matching the Test FAILED verdict.
_BADGE_PATTERN = re.compile(r"(?P<badge>(AUTH_FAILURE|QUOTA_EXHAUSTED|TEMP_RATE_LIMIT|NETWORK_FAILURE|TRANSIENT|MODEL_NOT_FOUND|UNKNOWN|CONFIG_ERROR)(?:\s+\(\d{3}\))?)")

# Endpoint compile lines: "compiled to 0 candidate(s)" -- count colored by
# class: 0 red (nothing routes), 1-5 yellow (suspiciously thin), else green.
_CANDIDATE_COUNT_PATTERN = re.compile(r"(?P<pre>compiled to\s+)(?P<n>\d+)(?P<post> candidate\(s\))")

# HTTP status codes on uvicorn access lines ("... HTTP/1.1\" 200"),
# colored by response class: 2xx green, 4xx yellow, 5xx red.
_STATUS_PATTERN = re.compile(r'(?P<prefix>HTTP/[\d.]+"\s)(?P<code>\d{3})(?P<suffix>\s*$)')
_STATUS_COLORS = {"2": _GREEN, "4": _YELLOW, "5": _RED}

# Messages that start a new visual "event" get a blank line before them:
# test outcomes and retry announcements (the operator just clicked Test
# and wants an unambiguous block) plus endpoint compile lines (startup's
# most scannable summary). Warnings/errors are spaced by level check.
_EVENT_MESSAGE_PREFIXES = ("Test OK", "Test FAILED", "Test for", "[", "Endpoint '")


class ColorFormatter(logging.Formatter):
    """Timestamped formatter with TTY-gated colors, phrase highlights,
    and blank-line grouping before test outcomes and warnings/errors.

    Base format is identical for every logger so all console lines line
    up; colors wrap existing fields, never rearrange them, and are only
    emitted when the formatter was built with use_colors=True.
    """

    def __init__(self, *, use_colors: bool | None = None, **kwargs) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
            **kwargs,
        )
        if use_colors is None:
            use_colors = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
            # FORCE_COLOR only fills in an unmade decision -- an explicit
            # use_colors=False (tests, piping) always wins.
            if os.environ.get("FORCE_COLOR"):
                use_colors = True
        self.use_colors = use_colors

    # -- coloring helpers -------------------------------------------------

    def _colorize_level(self, levelno: int, levelname: str) -> str:
        # Pad to 8 BEFORE wrapping so column alignment survives the escapes.
        padded = f"{levelname:<8}"
        if not self.use_colors:
            return padded
        color = _LEVEL_COLORS.get(levelno)
        return f"{color}{padded}{_RESET}" if color else padded

    def _colorize_name(self, name: str) -> str:
        return f"{_DIM}{name}{_RESET}" if self.use_colors else name

    def _colorize_message(self, message: str, levelno: int) -> str:
        """Highlight known phrases/status codes; tint the whole message
        for WARNING+ so the reason text reads at a glance."""
        if not self.use_colors:
            return message
        if levelno >= logging.WARNING:
            color = _LEVEL_COLORS.get(levelno, _YELLOW)
            # resume= re-opens the outer tint after each inner span's
            # reset, so the message stays uniformly tinted instead of
            # reverting to plain text mid-line.
            return self._highlight_spans(f"{color}{message}{_RESET}", resume=color)
        return self._highlight_spans(message)

    def _highlight_spans(self, message: str, *, resume: str = "") -> str:
        after = _RESET + resume

        for phrase, color in _HIGHLIGHT_COLORS.items():
            message = message.replace(phrase, f"{color}{phrase}{after}")

        def _account_sub(match: re.Match) -> str:
            return f"{_CYAN}{_BOLD}{match.group('tag')}{after}"

        def _model_sub(match: re.Match) -> str:
            return f"{_BOLD}{match.group('full')}{after}"

        def _badge_sub(match: re.Match) -> str:
            return f"{_RED}{_BOLD}{match.group('badge')}{after}"

        def _count_sub(match: re.Match) -> str:
            n = int(match.group("n"))
            color = _RED if n == 0 else (_YELLOW if n <= 5 else _GREEN)
            return f"{match.group('pre')}{color}{match.group('n')}{after}{match.group('post')}"

        def _code_sub(match: re.Match) -> str:
            code = match.group("code")
            color = _STATUS_COLORS.get(code[0], "")
            if not color:
                return match.group(0)
            return f"{match.group('prefix')}{color}{code}{after}{match.group('suffix')}"

        # Order matters: phrase replacements first, then the structured
        # patterns. The account-tag pattern must not run after the model
        # pattern has consumed "model 'x'" (they don't overlap, but tags
        # are checked before badges so [g/a] inside a badge-like context
        # still wins); count and status patterns are position-anchored to
        # their own phrases and can't collide.
        message = _ACCOUNT_TAG_PATTERN.sub(_account_sub, message)
        message = _MODEL_PATTERN.sub(_model_sub, message)
        message = _BADGE_PATTERN.sub(_badge_sub, message)
        message = _CANDIDATE_COUNT_PATTERN.sub(_count_sub, message)
        return _STATUS_PATTERN.sub(_code_sub, message)

    def _needs_event_spacing(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        return record.getMessage().startswith(_EVENT_MESSAGE_PREFIXES)

    # -- main --------------------------------------------------------------

    def format(self, record: logging.LogRecord) -> str:
        # Blank line BEFORE a new event group: prepend a newline to the
        # rendered line (after formatting), so the gap sits at column 0,
        # before the timestamp -- not between the prefix and the message.
        inject = self._needs_event_spacing(record)
        formatted = super().format(record)

        if not self.use_colors:
            return ("\n" + formatted) if inject else formatted

        # Rebuild the line with colored fields. The base render is
        # "{asctime} {levelname:<8} {name}: {message}"; split at the
        # (unique) padded levelname field and swap in colored spans.
        marker = f" {record.levelname:<8} "
        if marker not in formatted:
            return formatted
        head, rest = formatted.split(marker, 1)
        level_span = self._colorize_level(record.levelno, record.levelname)
        name_colon = f"{record.name}: "
        prefix = "\n" if inject else ""
        if rest.startswith(name_colon):
            message = rest[len(name_colon):]
            return (
                f"{prefix}{head} {level_span} {self._colorize_name(record.name)}: "
                f"{self._colorize_message(message, record.levelno)}"
            )
        return f"{prefix}{head} {level_span} {self._colorize_message(rest, record.levelno)}"


def _make_handler(use_colors: bool | None = None) -> logging.StreamHandler:
    """One handler definition shared by every logger this module configures."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter(use_colors=use_colors))
    return handler


def configure_logging(level: str = "INFO") -> None:
    root_logger = logging.getLogger("ai_gateway")
    root_logger.setLevel(level)

    if not root_logger.handlers:
        root_logger.addHandler(_make_handler())

    # Uvicorn's loggers (and the reloader process's "uvicorn.error") would
    # otherwise keep their pre-installed handlers with uvicorn's own
    # format. Replacing the handler AND disabling propagation is required:
    # propagation alone would double-print (uvicorn's handler + ours).
    # Levels are pinned to INFO explicitly: left at NOTSET they inherit
    # from ancestors (root defaults to WARNING), which silently drops
    # access/error INFO lines unless uvicorn's own config happens to set
    # the level first -- true under uvicorn.run(), false everywhere else.
    for name in UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = [_make_handler()]
        uv_logger.propagate = False
        uv_logger.setLevel(logging.INFO)
