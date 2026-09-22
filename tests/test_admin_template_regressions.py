from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "ai_gateway" / "admin" / "templates"


def test_make_reorderable_defined_before_content_block() -> None:
    """Regression test for a real bug: `makeReorderable` was once defined
    in a <script> block placed AFTER `{% block content %}{% endblock %}`.
    Several templates (provider_detail.html, endpoint_form.html) call
    makeReorderable(...) eagerly, at top-level script-parse time, inside
    their own content block -- not inside a DOMContentLoaded handler. When
    the function is defined later in document order, that call throws
    `ReferenceError: makeReorderable is not defined` and silently aborts
    the entire content-block <script>, killing everything after it in
    that script (in the observed case: the priority-models table never
    rendered, and the account-list drag/bulk-move setup never ran) with no
    visible error to the person using the admin UI.

    This test only checks source-text ordering, not actual JS execution
    (this project doesn't have a browser-based JS test harness), but it's
    a cheap, deterministic guard against the exact ordering mistake that
    caused this regression once already.
    """
    source = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")

    def_index = source.index("function makeReorderable")
    block_index = source.index("{% block content %}")

    assert def_index < block_index, (
        "makeReorderable must be defined before {% block content %} -- "
        "content templates call it eagerly at script-parse time, not "
        "after DOMContentLoaded, so defining it later breaks every page "
        "that uses it with a silent ReferenceError."
    )


def test_sortable_script_loaded_before_make_reorderable() -> None:
    """makeReorderable references the global `Sortable` at definition time
    (mounting the MultiDrag plugin) and at call time (Sortable.create).
    The <script src="...sortablejs..."> tag must appear before
    makeReorderable's own <script> block, or `Sortable` would be
    undefined when referenced.
    """
    source = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")

    sortable_script_index = source.index("sortablejs")
    def_index = source.index("function makeReorderable")

    assert sortable_script_index < def_index


def test_use_defaults_uncheck_seeds_blank_not_provider_defaults() -> None:
    """Regression test for a real reported bug: unchecking "Use this
    provider's default priority models" used to silently pre-fill the
    custom model editor with a copy of the provider's own default list
    (`ensureModelEditor(data.priority_models)`), instead of starting from
    a blank list with an explicit "add provider's default models" button
    to pull them in on demand. This only checks the source text of the
    `useDefaults` change handler specifically -- not full JS execution,
    since this project has no browser-based JS test harness -- but it's
    a cheap, deterministic guard against the exact regression.
    """
    source = (TEMPLATES_DIR / "endpoint_form.html").read_text(encoding="utf-8")

    handler_start = source.index("useDefaults.addEventListener")
    handler_end = source.index("});", handler_start)
    handler_body = source[handler_start:handler_end]

    assert "ensureModelEditor([])" in handler_body
    assert "data.priority_models" not in handler_body

    # The append-defaults button must exist and actually pull in the
    # provider's defaults -- otherwise unchecking would leave no way to
    # get them back into the list at all.
    assert "data-action=append-defaults" in source
    assert "data.priority_models" in source


def test_accounts_table_header_column_count_matches_js_injected_rows() -> None:
    """Regression test for a real reported bug: makeReorderable injects a
    drag-handle and a checkbox as two new leading cells into every body
    row, but the static <thead> was never updated to match, so every
    column after them appeared shifted by two (Username's header sitting
    over the checkbox/handle cells, Env var's header sitting over the
    actual username value, and so on).

    <thead> th count must equal raw server-rendered <td> count + 2 (the
    two cells makeReorderable injects at runtime: drag-handle, checkbox).
    """
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    out = env.get_template("provider_detail.html").render(
        provider_name="test-provider",
        errors=[],
        values={"litellm_prefix": "x/", "comment": "", "exclude_models_text": ""},
        models=[],
        models_json="[]",
        accounts=[
            {
                "key": "a",
                "username": "u@example.com",
                "env_var": "X",
                "api_key": None,
                "comment": None,
                "badge_class": "badge-ok",
                "state_label": "healthy",
            }
        ],
        live_models=[],
        excluded_live_models=[],
        test_model_options=[],
        request=None,
        success=None,
    )

    thead = out.split('<table id="accounts-table">')[1].split("<thead>")[1].split("</thead>")[0]
    th_count = thead.count("<th")

    tbody = out.split('<tbody id="accounts-tbody">')[1].split("</tbody>")[0]
    row = tbody.split('<tr data-account-key')[1].split("</tr>")[0]
    raw_td_count = row.count("<td")

    assert th_count == raw_td_count + 2, (
        f"accounts-table has {th_count} header cells but {raw_td_count} raw "
        f"row cells (+2 for the JS-injected drag-handle/checkbox = "
        f"{raw_td_count + 2} expected) -- header and body columns will be "
        f"visually misaligned."
    )


def test_models_table_header_column_count_is_nine() -> None:
    """Companion to the accounts-table check above, for the JS-rendered
    priority-models table. Its rows are built entirely client-side
    (createModelsTableController in provider_detail.html), so the row
    cell count can't be verified via a server-side render the way
    accounts-table's can -- this pins the expected header count instead:
    2 JS-injected (drag-handle, checkbox) + 7 real columns (order
    buttons, pattern, enabled-toggle, comment, test, move-to-standard,
    remove) = 9. If a column is added to createModelsTableController's
    row without updating this header (or this number), they'll silently
    drift out of alignment again.
    """
    source = (TEMPLATES_DIR / "provider_detail.html").read_text(encoding="utf-8")
    thead = source.split('<table class="models-table">')[1].split("<thead>")[1].split("</thead>")[0]
    th_count = thead.count("<th")
    assert th_count == 9


def test_standard_models_table_header_column_count_is_eight() -> None:
    """Same idea as the priority-list check above, for the standard
    models table: 2 JS-injected (drag-handle, checkbox) + 6 real columns
    (order buttons, pattern, comment, test, move-to-priority, remove) =
    8 -- one fewer than priority's 9, since the standard list has no
    enabled/disabled toggle (an entry there is already inherently
    inactive).
    """
    source = (TEMPLATES_DIR / "provider_detail.html").read_text(encoding="utf-8")
    # Second occurrence of the models-table markup is the standard list.
    thead = source.split('<table class="models-table">')[2].split("<thead>")[1].split("</thead>")[0]
    th_count = thead.count("<th")
    assert th_count == 8
