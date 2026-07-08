from castcode.commands import (
    CommandRow,
    help_text,
    local_rows,
    popup_rows,
    route,
    spec_for,
)


def test_aliases_route_to_same_handler():
    handlers = {route(name).spec.handler for name in ("/switch", "/resume", "/sessions")}

    assert handlers == {"switcher"}
    assert route("/clear").spec.handler == "new"


def test_unknown_slash_is_passthrough():
    routed = route("/unknown  arg")

    assert routed.command == "/unknown"
    assert routed.args == "arg"
    assert routed.spec is None
    assert routed.passthrough is True


def test_non_slash_has_no_local_route():
    assert route("hello") is None


def test_multiline_input_still_routes_local_command():
    # shift+enter puts a newline right after the command token; that must not
    # demote a local command to SDK passthrough (a paid query).
    routed = route("/help\nmore text")

    assert routed.spec.handler == "help"
    assert routed.args == "more text"
    assert routed.passthrough is False


def test_multiline_unknown_slash_passthrough_keeps_command_token():
    routed = route("/unknown\narg")

    assert routed.command == "/unknown"
    assert routed.args == "arg"
    assert routed.passthrough is True


def test_exclusive_command_metadata():
    assert spec_for("/new").exclusive is True
    assert spec_for("/clear").exclusive is True
    assert spec_for("/fork").exclusive is True
    assert spec_for("/help").exclusive is False


def test_help_text_generated_from_registry():
    text = help_text()

    for row in local_rows():
        assert row.name in text
        assert row.description in text
    assert "ctrl+r" in text
    assert "shift+tab" in text
    assert "escape" in text


def test_popup_rows_do_not_duplicate_shadowed_sdk_names():
    rows = popup_rows([
        {"name": "/help", "description": "sdk help"},
        {"name": "/doctor", "description": "diagnostics"},
    ])

    names = [row.name for row in rows]
    assert names.count("/help") == 1
    assert "/doctor" in names


def test_popup_rows_group_local_sdk_then_plugin():
    rows = popup_rows([
        {"name": "/plugin:run", "description": "plugin"},
        CommandRow("/review", "review", "skill"),
        {"name": "/doctor", "description": "diagnostics"},
    ])

    names = [row.name for row in rows]
    assert names.index("/help") < names.index("/doctor")
    assert names.index("/doctor") < names.index("/review")
    assert names.index("/review") < names.index("/plugin:run")
    assert rows[names.index("/plugin:run")].source == "plugin"


def test_popup_rows_accept_command_row_and_prefix_filter():
    rows = popup_rows([CommandRow("/sdk:thing", "sdk thing", "sdk")], "/sdk")

    assert rows == [CommandRow("/sdk:thing", "sdk thing", "plugin")]


def test_popup_rows_include_aliases():
    rows = popup_rows([], "/res")

    assert rows == [
        CommandRow("/resume", "Resume a conversation", "local", "switcher")
    ]
