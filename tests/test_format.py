"""Characterization: castcode.format pure helpers, tested DIRECTLY."""

import os

from rich.text import Text

import castcode.format as fmt


# --- tilde_path --------------------------------------------------------------

def test_tilde_collapses_home():
    assert fmt.tilde_path(fmt._HOME) == "~"
    assert fmt.tilde_path(fmt._HOME + "/Code/x") == "~/Code/x"
    assert fmt.tilde_path("/etc/passwd") == "/etc/passwd"


def test_tilde_requires_sep_boundary():
    # A path that merely starts with _HOME but is not a child dir is NOT collapsed.
    assert fmt.tilde_path(fmt._HOME + "_sibling") == fmt._HOME + "_sibling"


# --- rel_path ----------------------------------------------------------------

def test_rel_cwd_itself_is_dot():
    assert fmt.rel_path(fmt.CWD) == "."


def test_rel_strips_cwd_prefix():
    assert fmt.rel_path(fmt.CWD + "/sub/x.py") == "sub/x.py"


def test_rel_falls_through_to_tilde_for_home_paths():
    assert fmt.rel_path(fmt._HOME + "/foo") == "~/foo"


def test_rel_passes_through_unrelated_absolute_path():
    assert fmt.rel_path("/etc/passwd") == "/etc/passwd"


def test_rel_requires_sep_boundary():
    # Starts with CWD but no separator boundary -> NOT cwd-stripped; delegates to
    # tilde_path. (A regression using startswith(CWD) instead of CWD + os.sep would
    # mis-strip to "ibling".)
    sibling = fmt.CWD + "_sibling"
    assert fmt.rel_path(sibling) == fmt.tilde_path(sibling)
    assert fmt.rel_path(sibling) != "_sibling"


# --- CWD / _HOME import-time snapshots ----------------------------------------

def test_cwd_home_are_import_time_snapshots(monkeypatch, tmp_path):
    before = fmt.CWD
    monkeypatch.chdir(tmp_path)
    # CWD is a module-level snapshot taken at import; chdir must NOT change it.
    assert fmt.CWD == before
    assert fmt.CWD != str(tmp_path)
    assert os.path.isabs(fmt.CWD)
    assert os.path.isabs(fmt._HOME)


# --- gradient_banner ---------------------------------------------------------

def test_banner_is_composed_from_swappable_parts():
    assert fmt.BANNER == fmt._join_art(fmt.LOGO_CAST, fmt.LOGO_CODE)
    assert fmt.BANNER_STACKED == fmt._stack_art(fmt.LOGO_CAST, fmt.LOGO_CODE)


def test_banner_join_trims_extra_inner_whitespace():
    assert fmt.BANNER_GAP == ""
    left_lines = fmt._art_lines(fmt.LOGO_CAST)
    right_lines = fmt._art_lines(fmt.LOGO_CODE)
    left_width = fmt._art_width(left_lines)
    for row, left, right in zip(fmt.BANNER.splitlines(), left_lines, right_lines):
        prefix = f"{left.ljust(left_width)}{fmt.BANNER_GAP}"
        assert row.startswith(prefix)
        assert row[len(prefix):] == right.rstrip()


def test_stacked_banner_has_blank_line_between_halves():
    cast_lines = fmt._art_lines(fmt.LOGO_CAST)
    stacked_lines = fmt.BANNER_STACKED.splitlines()
    assert stacked_lines[len(cast_lines)] == ""


def test_banner_for_width_stacks_when_full_logo_does_not_fit():
    assert fmt.banner_for_width(fmt.BANNER_WIDTH) == fmt.BANNER
    assert fmt.banner_for_width(fmt.BANNER_WIDTH - 1) == fmt.BANNER_STACKED


def test_gradient_banner_keeps_plain_figlet():
    text = fmt.gradient_banner("#ff0000", "#0000ff")
    assert isinstance(text, Text)
    assert text.plain == fmt.BANNER


def test_gradient_banner_uses_stacked_figlet_for_narrow_width():
    text = fmt.gradient_banner("#ff0000", "#0000ff", width=fmt.BANNER_WIDTH - 1)
    assert isinstance(text, Text)
    assert text.plain == fmt.BANNER_STACKED


def test_gradient_banner_has_per_char_varying_styling():
    # The per-char gradient is the sanctioned CSS-styling exception. Guard against a
    # silent regression to unstyled / flat-colored text: every non-newline char is
    # appended with its own color, so spans exist AND the colors vary across columns.
    text = fmt.gradient_banner("#ff0000", "#0000ff")
    assert len(text.spans) > 0
    styles = {str(span.style) for span in text.spans}
    assert len(styles) > 1
