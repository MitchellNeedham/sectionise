"""Behaviour of the pure core, written to be read as config -> input -> output.

The `CASES` table is the heart of the suite: each row shows the settings, the
source line(s), and exactly what they become. `test_transform` checks the
rewrite and that re-running it is a no-op. Everything a banner should *not*
touch appears as a row whose output equals its input.
"""

from collections import namedtuple

import pytest

from sectionise import core
from sectionise.core import Style

HASH = ("#", "")
SLASH = ("//", "")
HTML = ("<!--", "-->")
C = core.syntax_for(".c")  # both // and /* */

Case = namedtuple("Case", "name style syntax src want")

# ------------------------------ Transform table ------------------------------
CASES = [
    Case(
        "centre from both sides",
        dict(width=40), HASH,
        "# ------- Loading -------\n",
        "# -------------- Loading ---------------\n",
    ),
    Case(
        "centre from trailing fill only",
        dict(width=40), HASH,
        "# Ancillary functions ------------------------------\n",
        "# -------- Ancillary functions ---------\n",
    ),
    Case(
        "centre from leading fill only",
        dict(width=40), HASH,
        "# ------------------------------ Ancillary functions\n",
        "# -------- Ancillary functions ---------\n",
    ),
    Case(
        "unicode em-dash fill becomes ascii",
        dict(width=40), HASH,
        "# ——— Loading ———\n",
        "# -------------- Loading ---------------\n",
    ),
    Case(
        "hash fill becomes dashes",
        dict(width=40), HASH,
        "##### basic #####\n",
        "# --------------- basic ----------------\n",
    ),
    Case(
        "left aligned title leads, fill trails",
        dict(width=40, align="left"), HASH,
        "# --- Setup ---\n",
        "# Setup --------------------------------\n",
    ),
    Case(
        "box collapses to single",
        dict(width=40, style="single"), HASH,
        "# ======\n# Setup\n# ======\n",
        "# --------------- Setup ----------------\n",
    ),
    Case(
        "single expands to box",
        dict(width=40, style="box"), HASH,
        "# --- Setup ---\n",
        "# --------------------------------------\n# Setup\n# --------------------------------------\n",
    ),
    Case(
        "slash line comment",
        dict(width=40), SLASH,
        "// --- Region ---\n",
        "// -------------- Region ---------------\n",
    ),
    Case(
        "c block comment stays a block comment",
        dict(width=40), C,
        "/* --- Setup --- */\n",
        "/* ------------- Setup -------------- */\n",
    ),
    Case(
        "html block comment",
        dict(width=40), HTML,
        "<!-- --- Part --- -->\n",
        "<!-- ------------ Part ------------- -->\n",
    ),
    Case(
        "trailing lone hash does not leak into title",
        dict(width=60), HASH,
        "# ---------------- model config ---------------- #\n",
        "# ---------------------- model config ----------------------\n",
    ),
    Case(
        "title-less divider left alone by default",
        dict(width=40), HASH,
        "# ==============================\n",
        "# ==============================\n",
    ),
    Case(
        "title-less divider normalised when dividers on",
        dict(width=40, dividers=True), HASH,
        "# ==============================\n",
        "# --------------------------------------\n",
    ),
    Case(
        "indentation preserved",
        dict(width=40), HASH,
        "    # ---- nested ----\n",
        "    # ------------- nested -------------\n",
    ),
    Case(
        "ordinary prose left alone",
        dict(), HASH,
        "# a normal comment\n",
        "# a normal comment\n",
    ),
    Case(
        "crlf line ending preserved",
        dict(width=40), HASH,
        "# --- Setup ---\r\n",
        "# --------------- Setup ----------------\r\n",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_transform(case):
    out, _, errors = core.process_text(case.src, case.syntax, Style(**case.style))
    assert out == case.want
    assert errors == []
    # Re-running over the output is a no-op (idempotent).
    again, changed, _ = core.process_text(out, case.syntax, Style(**case.style))
    assert again == out
    assert changed == 0


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_changed_flag_matches_whether_output_differs(case):
    _, changed, _ = core.process_text(case.src, case.syntax, Style(**case.style))
    assert bool(changed) == (case.want != case.src)


# ------------------------------ Ignored inputs -------------------------------
@pytest.mark.parametrize(
    "src",
    [
        "x = 1  # trailing note\n",
        '# print("=== run ===")\n',
        "# short --\n",  # fill run below min_run
        "plain code line\n",
    ],
)
def test_left_alone(src):
    out, changed, errors = core.process_text(src, HASH, Style())
    assert out == src
    assert changed == 0
    assert errors == []


# ---------------------------- String protection -----------------------------
def test_banner_inside_python_string_is_left_untouched():
    text = 'x = """\n# ==== not a comment ====\nhello\n"""\n'
    protected = core.protected_lines(text, ".py")
    out, changed, _ = core.process_text(text, HASH, Style(), protected=protected)
    assert out == text
    assert changed == 0


def test_banner_after_python_string_still_rewritten():
    text = 'x = """\n# ==== data ====\n"""\n# --- Real section ---\n'
    protected = core.protected_lines(text, ".py")
    out, changed, _ = core.process_text(text, HASH, Style(width=40), protected=protected)
    assert changed == 1
    assert "# ==== data ====" in out
    assert out.splitlines()[-1] == core._format_banner("", HASH, "Real section", Style(width=40))


def test_unparseable_python_falls_back_to_heuristic():
    text = 'def broken(:\n    x = """\n# ==== nope ====\n"""\n'
    assert 2 in core.protected_lines(text, ".py")


def test_backtick_template_literal_protects_banner():
    text = "const s = `\n// ==== not a comment ====\n`;\n"
    protected = core.protected_lines(text, ".ts")
    out, changed, _ = core.process_text(text, SLASH, Style(width=60), protected=protected)
    assert out == text
    assert changed == 0


# ------------------------------- Box handling --------------------------------
def test_boxes_disabled_leaves_box_untouched():
    text = "# ======\n# Setup\n# ======\n"
    out, changed, _ = core.process_text(text, HASH, Style(boxes=False, width=40))
    assert out == text
    assert changed == 0


def test_mismatched_rules_around_comment_not_merged():
    text = "# =====================\n# TODO fix this later\n# ---------------------\n"
    out, changed, _ = core.process_text(text, HASH, Style(width=40))
    assert out == text
    assert changed == 0


# ---------------------------- Over-long titles -------------------------------
def test_too_long_single_title_errors_and_leaves_unchanged():
    text = "# --- a very long section title indeed ---\n"
    out, changed, errors = core.process_text(text, HASH, Style(width=20))
    assert out == text
    assert changed == 0
    assert len(errors) == 1 and "box style" in errors[0]


def test_box_style_accommodates_longer_titles():
    text = "# --- a very long section title indeed ---\n"
    _, _, errors = core.process_text(text, HASH, Style(style="box", width=60))
    assert errors == []


# ------------------------------- Alignment -----------------------------------
def test_left_align_is_idempotent():
    style = Style(width=40, align="left")
    once = core._format_banner("", HASH, "Section name", style)
    _, changed, _ = core.process_text(once + "\n", HASH, style)
    assert changed == 0


def test_align_center_normalised_from_american_spelling():
    assert Style(align="center").align == "centre"


def test_tab_indent_counts_as_configured_columns():
    out, _, _ = core.process_text("\t# --- nested ---\n", HASH, Style(width=40, tab_width=8))
    line = out.rstrip("\n")
    assert line.startswith("\t# ")
    assert len(line.expandtabs(8)) == 40


# ------------------------------- Validation ----------------------------------
@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(width=0), "width"),
        (dict(width=-5), "width"),
        (dict(min_run=0), "min_run"),
        (dict(fill="--"), "fill"),
        (dict(fill=" "), "fill"),
        (dict(detect_chars=""), "detect_chars"),
        (dict(style="banner"), "style"),
        (dict(align="middle"), "align"),
        (dict(tab_width=0), "tab_width"),
        (dict(max_title=0), "max_title"),
    ],
)
def test_invalid_settings_raise_clear_errors(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Style(**kwargs)


def test_fill_added_to_detect_chars_for_idempotency():
    style = Style(fill="+", detect_chars="-=")
    assert "+" in style.detect_chars
    once = core._format_banner("", HASH, "Title", style)
    _, changed, _ = core.process_text(once + "\n", HASH, style)
    assert changed == 0


# ---------------------------- Language registry ------------------------------
@pytest.mark.parametrize(
    "suffix, language, width",
    [
        (".py", "python", 88),
        (".js", "javascript", 80),
        (".ts", "typescript", 80),
        (".rs", "rust", 100),
        (".go", "go", 100),
        (".java", "java", 100),
        (".cs", "csharp", 120),
        (".rb", "ruby", 120),
        (".css", "css", 80),
    ],
)
def test_language_registry(suffix, language, width):
    assert core.language_for(suffix) == language
    assert core.language_defaults(language)["width"] == width


def test_shebang_language_detection():
    assert core.shebang_language("#!/usr/bin/env python3\n") == "python"
    assert core.shebang_language("#!/bin/bash\n") == "shell"
    assert core.shebang_language("#!/usr/bin/node\n") == "javascript"
    assert core.shebang_language("# just a comment\n") is None
