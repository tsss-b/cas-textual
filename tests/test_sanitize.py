import time

from castcode.tool_render.sanitize import (
    _bounded_lines,
    _clean_line,
    _join_capped,
    _tail_lines,
    one_line,
)


# --- _join_capped: the off-by-one-prone newline accounting ---


def test_join_capped_single_line_exact_cap():
    out = _join_capped(["abcde"], 5)
    assert out == "abcde"
    assert len(out) == 5


def test_join_capped_single_line_over_cap_is_cropped():
    out = _join_capped(["abcdef"], 5)
    assert len(out) <= 5
    assert out.endswith("…")


def test_join_capped_multiline_never_exceeds_cap_including_separators():
    # The separator ("\n") between joined lines must be counted against max_chars.
    # Probe a range of caps against many candidate lines so any line that fits
    # plus its preceding newline is fully accounted for.
    lines = ["aaaa", "bbbb", "cccc", "dddd", "eeee"]
    for cap in range(1, 40):
        out = _join_capped(lines, cap)
        assert len(out) <= cap, (cap, repr(out))


def test_join_capped_two_lines_exact_cap_with_newline():
    # "ab" + "\n" + "cd" == 5 chars total; cap of 5 must hold both with separator.
    out = _join_capped(["ab", "cd"], 5)
    assert out == "ab\ncd"
    assert len(out) == 5


def test_join_capped_zero_cap_is_empty():
    assert _join_capped(["abc", "def"], 0) == ""


# --- _bounded_lines: empty/whitespace handling ---


def test_bounded_lines_skips_empty_and_whitespace_only_lines():
    text = "real\n\n   \n\t\nalso real\n"
    out = _bounded_lines(text, max_lines=10, max_chars=200)
    assert out.splitlines() == ["real", "also real"]


def test_bounded_lines_all_whitespace_is_empty():
    out = _bounded_lines("   \n\t\n\n", max_lines=10, max_chars=200)
    assert out == ""


def test_bounded_lines_zero_limits_are_empty():
    assert _bounded_lines("abc\ndef", max_lines=0, max_chars=200) == ""
    assert _bounded_lines("abc\ndef", max_lines=10, max_chars=0) == ""


# --- _bounded_lines(first_last=True): head + … + tail shape ---


def test_bounded_lines_first_last_head_ellipsis_tail_shape():
    text = "\n".join(f"line {i}" for i in range(20))
    out = _bounded_lines(text, max_lines=6, max_chars=400, first_last=True)
    rows = out.splitlines()
    assert len(rows) == 6
    assert rows[:3] == ["line 0", "line 1", "line 2"]
    assert rows[3] == "…"
    assert rows[4:] == ["line 18", "line 19"]


def test_bounded_lines_first_last_under_limit_has_no_ellipsis():
    text = "\n".join(f"line {i}" for i in range(4))
    out = _bounded_lines(text, max_lines=6, max_chars=400, first_last=True)
    assert out.splitlines() == ["line 0", "line 1", "line 2", "line 3"]
    assert "…" not in out


def test_bounded_lines_first_last_small_limit_replaces_last_line():
    # max_lines < 4 cannot use head+tail shape; overflow collapses the last line.
    text = "\n".join(f"line {i}" for i in range(10))
    out = _bounded_lines(text, max_lines=3, max_chars=400, first_last=True)
    rows = out.splitlines()
    assert len(rows) == 3
    assert rows[-1] == "…"


# --- _tail_lines: long last lines ---


def test_tail_lines_returns_last_n_lines():
    text = "\n".join(f"line {i}" for i in range(20))
    tail = _tail_lines(text, 3, max_chars=400)
    assert tail == ["line 17", "line 18", "line 19"]


def test_tail_lines_crops_long_last_line():
    text = "head\n" + "z" * 5000
    tail = _tail_lines(text, 1, max_chars=100)
    assert len(tail) == 1
    assert len(tail[0]) <= 100
    assert tail[0].endswith("…")


def test_tail_lines_marks_head_cut_line_even_when_cleaned_fits():
    # Last line longer than the scan window, with enough trailing whitespace
    # that the cleaned slice fits max_chars: the head cut must still be marked
    # instead of silently rendering a line that starts mid-way.
    text = "head\n" + "y" * 300 + "x" * 96 + " " * 260
    tail = _tail_lines(text, 1, max_chars=100)
    assert tail == ["x" * 96 + "…"]


def test_tail_lines_zero_count_is_empty():
    assert _tail_lines("a\nb\nc", 0, max_chars=100) == []


def test_tail_lines_skips_trailing_whitespace_lines():
    text = "alpha\nbeta\n\n   \n"
    tail = _tail_lines(text, 2, max_chars=100)
    assert tail == ["alpha", "beta"]


# --- huge single-line input: scan windows stay bounded ---


def test_one_line_huge_single_line_is_bounded_and_prompt():
    huge = "x" * 5_000_000
    start = time.monotonic()
    out = one_line(huge, 1200)
    elapsed = time.monotonic() - start
    assert len(out) <= 1200
    assert out.endswith("…")
    assert elapsed < 1.0


def test_bounded_lines_huge_single_line_is_bounded():
    huge = "y" * 5_000_000
    out = _bounded_lines(huge, max_lines=6, max_chars=1200)
    assert len(out) <= 1200


def test_tail_lines_huge_single_line_is_bounded():
    huge = "z" * 5_000_000
    tail = _tail_lines(huge, 3, max_chars=1200)
    # A single line yields at most one tail entry, and it must be cropped.
    assert len(tail) <= 1
    if tail:
        assert len(tail[0]) <= 1200


# --- control / ANSI stripping and tab / CR normalization ---


def test_clean_line_strips_ansi():
    out = _clean_line("\x1b[31mred\x1b[0m text", 100)
    assert "\x1b" not in out
    assert out == "red text"


def test_clean_line_strips_control_chars():
    out = _clean_line("a\x00b\x07c\x7fd", 100)
    assert out == "abcd"


def test_clean_line_normalizes_tabs_and_carriage_returns():
    # _clean_line drops CR and expands tabs but leaves any LF intact
    # (line splitting happens upstream in _raw_lines, not here).
    out = _clean_line("a\tb\r\nc", 100)
    assert "\t" not in out
    assert "\r" not in out
    assert out == "a    b\nc"


def test_clean_line_tab_becomes_four_spaces():
    assert _clean_line("\tx", 100) == "    x"


def test_clean_line_strips_tool_use_error_markup():
    out = _clean_line("<tool_use_error>boom</tool_use_error>", 100)
    assert out == "boom"


def test_clean_line_strips_generic_error_markup():
    assert _clean_line("<error>ECONNREFUSED</error>", 100) == "ECONNREFUSED"
    assert _clean_line('<error code="404">not found</error>', 100) == "not found"


def test_bounded_lines_strips_ansi_and_controls_across_lines():
    text = "\x1b[31mone\x00\nt\two\r\n"
    out = _bounded_lines(text, max_lines=10, max_chars=200)
    rows = out.splitlines()
    assert "\x1b" not in out
    assert "\x00" not in out
    assert "\r" not in out
    assert "\t" not in out
    assert rows == ["one", "t    wo"]
