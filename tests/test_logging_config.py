from __future__ import annotations

import logging
import re

import pytest

from ai_gateway.logging_config import UVICORN_LOGGERS, ColorFormatter, configure_logging

_ANSI = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


@pytest.fixture
def restore_logging():
    """Snapshot and restore the handlers/levels of every logger this module
    touches. main.py's lifespan calls configure_logging at app startup, so
    other tests DO see these loggers -- without restoration, this test's
    reconfiguration (and the test order) could leak into them.
    """
    watched = ["ai_gateway", *UVICORN_LOGGERS]
    saved = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).propagate,
            logging.getLogger(name).level,
        )
        for name in watched
    }
    yield
    for name, (handlers, propagate, level) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers = handlers
        lg.propagate = propagate
        lg.setLevel(level)


def _record(name: str, level: int, msg: str, *args) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=level, pathname="x.py", lineno=1, msg=msg, args=args or None, exc_info=None
    )


def test_configure_logging_attaches_shared_formatter_to_uvicorn_loggers(restore_logging) -> None:
    """Every console line must carry timestamp, level, and logger name --
    including uvicorn's access/startup lines, which used to print as
    `INFO:     127.0.0.1:x - "POST ..." 200 OK` with no timestamp,
    visually fragmenting the output against the app's own lines.
    """
    configure_logging("INFO")

    for name in UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        assert uv_logger.handlers, f"{name} has no handler"
        assert len(uv_logger.handlers) == 1
        assert uv_logger.propagate is False, f"{name} must not propagate (would double-print)"
        fmt = uv_logger.handlers[0].formatter
        assert isinstance(fmt, ColorFormatter)
        rendered = _strip_ansi(fmt.format(_record(name, logging.INFO, "hello %s", "world")))
        assert "INFO" in rendered
        assert "hello world" in rendered
        assert rendered.startswith("20")
        assert f"{name}:" in rendered

    app_logger = logging.getLogger("ai_gateway")
    assert app_logger.handlers
    assert isinstance(app_logger.handlers[0].formatter, ColorFormatter)


def test_configure_logging_is_idempotent(restore_logging) -> None:
    """configure_logging runs once per app startup, but tests (and hot
    reloads) can trigger it repeatedly -- a second call must not stack
    duplicate handlers, or every line prints twice.
    """
    configure_logging("INFO")
    app_count = len(logging.getLogger("ai_gateway").handlers)
    uvicorn_counts = {name: len(logging.getLogger(name).handlers) for name in UVICORN_LOGGERS}

    configure_logging("INFO")

    assert len(logging.getLogger("ai_gateway").handlers) == app_count
    for name, count in uvicorn_counts.items():
        assert len(logging.getLogger(name).handlers) == count


def test_uvicorn_loggers_get_explicit_info_level(restore_logging) -> None:
    """Regression test for a bug found by a live render: with no explicit
    level, uvicorn's loggers stay NOTSET and inherit from root (WARNING
    by default), so access/error INFO lines were silently DROPPED at the
    logger -- before any handler/formatter ever ran. True under
    uvicorn.run() only because uvicorn's own config sets the level first;
    false in every other embedding (reloader child, scripts, tests).
    """
    logging.getLogger("uvicorn.access").setLevel(logging.NOTSET)

    configure_logging("INFO")

    assert logging.getLogger("uvicorn.access").getEffectiveLevel() == logging.INFO
    assert logging.getLogger("uvicorn").getEffectiveLevel() == logging.INFO
    assert logging.getLogger("uvicorn.error").getEffectiveLevel() == logging.INFO


def test_uvicorn_access_line_renders_with_timestamp_and_logger_name(restore_logging) -> None:
    """End-to-end render of a real access-style record through the shared
    handler: the exact line shape operators see for request logging.
    """
    configure_logging("INFO")

    access_logger = logging.getLogger("uvicorn.access")
    record = _record(
        "uvicorn.access",
        logging.INFO,
        '%s - "%s %s HTTP/1.1" %d',
        "127.0.0.1:60454",
        "POST",
        "/admin/providers/gemini/accounts/one_anon_101/test",
        200,
    )
    rendered = _strip_ansi(access_logger.handlers[0].formatter.format(record))

    assert rendered.startswith("20")
    assert "INFO" in rendered
    assert "uvicorn.access:" in rendered
    assert "POST /admin/providers/gemini/accounts/one_anon_101/test HTTP/1.1" in rendered
    assert "200" in rendered


# -- ColorFormatter ----------------------------------------------------------


def test_plain_formatter_has_no_ansi_escapes() -> None:
    fmt = ColorFormatter(use_colors=False)
    out = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "Test OK for gemini/personal"))
    assert "\033[" not in out
    assert "Test OK for gemini/personal" in out


def test_colored_formatter_wraps_level_and_message() -> None:
    fmt = ColorFormatter(use_colors=True)
    out = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "Test OK for gemini/personal"))
    assert "\033[" in out
    plain = _strip_ansi(out)
    # Colors wrap fields; the plain text must be unchanged.
    assert "INFO" in plain
    assert "Test OK for gemini/personal" in plain
    # The phrase itself is highlighted.
    assert "\033[1m" in out and "\033[32m" in out


def test_level_colors_by_severity() -> None:
    fmt = ColorFormatter(use_colors=True)
    info_out = fmt.format(_record("x", logging.INFO, "plain info"))
    warn_out = fmt.format(_record("x", logging.WARNING, "careful"))
    error_out = fmt.format(_record("x", logging.ERROR, "broken"))
    assert "\033[33m" in warn_out  # yellow
    assert "\033[31m" in error_out  # red
    assert "\033[32m" in info_out  # green


def test_test_outcome_phrases_colored() -> None:
    fmt = ColorFormatter(use_colors=True)
    ok = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[gemini/personal] Test OK -- model 'gemini-2.5-flash'"))
    failed = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[gemini/one_anon_301] Test FAILED -- model 'gemini-3.6-flash': AUTH_FAILURE (403) -- denied"))
    retry = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[gemini/personal/m] Test failed with TRANSIENT; retrying once after 1.0s."))
    assert "\033[32m\033[1mTest OK" in ok
    assert "\033[31m\033[1mTest FAILED" in failed
    assert "\033[33m\033[1mretrying once" in retry


def test_account_tag_bold_cyan_and_leads_line() -> None:
    """The [provider/account] tag must be highlighted so the tested
    account's identity reads at column 0 of every test-event line."""
    fmt = ColorFormatter(use_colors=True)
    out = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[gemini/one_anon_101] Test OK -- model 'x'"))
    # Bold cyan tag, then reset.
    assert "\033[36m\033[1m[gemini/one_anon_101]\033[0m" in out
    # Tag comes first in the rendered message (after the prefix), before
    # the verdict phrase.
    plain = _strip_ansi(out)
    assert plain.index("[gemini/one_anon_101]") < plain.index("Test OK")


def test_model_name_bold() -> None:
    fmt = ColorFormatter(use_colors=True)
    out = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[g/a] Test OK -- model 'gemini-2.5-flash'"))
    assert "\033[1mmodel 'gemini-2.5-flash'\033[0m" in out


def test_failure_badge_and_status_bold_red() -> None:
    fmt = ColorFormatter(use_colors=True)
    out = fmt.format(
        _record("ai_gateway.admin.testing", logging.INFO, "[g/a] Test FAILED -- model 'm': AUTH_FAILURE (403) -- denied")
    )
    assert "\033[31m\033[1mAUTH_FAILURE (403)\033[0m" in out


def test_candidate_count_colored_by_class() -> None:
    """Endpoint compile lines: 0 red (nothing routes), 1-5 yellow (thin),
    6+ green."""
    fmt = ColorFormatter(use_colors=True)

    def render(n: int) -> str:
        return fmt.format(_record("ai_gateway.candidates", logging.INFO, "Endpoint 'e' compiled to %d candidate(s).", n))

    assert "\033[31m0\033[0m candidate(s)" in render(0)
    assert "\033[33m3\033[0m candidate(s)" in render(3)
    assert "\033[32m139\033[0m candidate(s)" in render(139)
    # Plain-text content unchanged.
    assert "compiled to 0 candidate(s)" in _strip_ansi(render(0))


def test_access_line_status_code_colored_by_class() -> None:
    fmt = ColorFormatter(use_colors=True)

    def render(code: int) -> str:
        return fmt.format(
            _record(
                "uvicorn.access",
                logging.INFO,
                '%s - "%s %s HTTP/1.1" %d',
                "127.0.0.1:1",
                "GET",
                "/x",
                code,
            )
        )

    assert "\033[32m200" in render(200)  # green
    assert "\033[33m403" in render(403)  # yellow
    assert "\033[31m503" in render(503)  # red
    assert "\033[32m" not in _strip_ansi(render(200))


def test_event_spacing_blank_line_before_outcomes_and_warnings() -> None:
    fmt = ColorFormatter(use_colors=False)

    ok = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[gemini/personal] Test OK -- model 'm'"))
    failed = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[gemini/x] Test FAILED -- model 'm': CONFIG_ERROR -- bad"))
    retry = fmt.format(_record("ai_gateway.admin.testing", logging.INFO, "[g/a/m] Test failed with TRANSIENT; retrying once after 1.0s."))
    warn = fmt.format(_record("ai_gateway.candidates", logging.WARNING, "model not found"))
    compile_line = fmt.format(_record("ai_gateway.candidates", logging.INFO, "Endpoint 'e' compiled to 12 candidate(s)."))
    plain = fmt.format(_record("uvicorn.access", logging.INFO, "127.0.0.1 - \"GET /x HTTP/1.1\" 200"))

    assert ok.startswith("\n")
    assert failed.startswith("\n")
    # The retry announcement leads with the [tag] too -- it must be
    # spaced by its tag prefix, not by the mid-line "retrying once" phrase.
    assert retry.startswith("\n")
    assert warn.startswith("\n")
    # Endpoint compile lines start a new visual block (startup summary).
    assert compile_line.startswith("\n")
    # Ordinary lines stay gapless.
    assert not plain.startswith("\n")


def test_spacing_leaves_record_unmutated() -> None:
    """The formatter must not permanently mutate the record it renders
    (handlers can share records, e.g. when a log shipper reads them
    after us)."""
    fmt = ColorFormatter(use_colors=False)
    record = _record("ai_gateway.admin.testing", logging.INFO, "[gemini/personal] Test OK -- model 'm'")
    fmt.format(record)
    assert record.msg == "[gemini/personal] Test OK -- model 'm'"
    assert record.args is None


def test_no_color_env_disables_colors(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert ColorFormatter(use_colors=None).use_colors is False


def test_force_color_env_forces_colors(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert ColorFormatter(use_colors=None).use_colors is True


def test_use_colors_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    # Explicit False wins even over FORCE_COLOR (tests, piping).
    assert ColorFormatter(use_colors=False).use_colors is False
    # But an unmade decision (use_colors=None) still honors FORCE_COLOR.
    assert ColorFormatter(use_colors=None).use_colors is True
