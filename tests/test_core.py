import pytest

from sectionise import core
from sectionise.core import Style

HASH = ("#", "")
SLASH = ("//", "")
HTML = ("<!--", "-->")


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"width": 0}, "width"),
        ({"width": -5}, "width"),
        ({"min_run": 0}, "min_run"),
        ({"fill": "--"}, "fill"),
        ({"fill": " "}, "fill"),
        ({"detect_chars": ""}, "detect_chars"),
        ({"style": "banner"}, "style"),
        ({"max_title": 0}, "max_title"),
    ],
)
def test_invalid_settings_raise_clear_errors(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Style(**kwargs)


def test_fill_is_added_to_detect_chars_for_idempotency():
    style = Style(fill="+", detect_chars="-=")
    assert "+" in style.detect_chars
    # A banner written with the fill is then re-detected and left unchanged.
    once = core._format_banner("", HASH, "Title", style)
    new, changed, _ = core.process_text(once + "\n", HASH, style)
    assert changed == 0


def test_single_banner_centres_and_fits_width():
    out = core._format_banner("", HASH, "Loading", Style(width=40))
    assert len(out) == 40
    assert out.startswith("# ")
    assert " Loading " in out


def test_single_banner_is_idempotent():
    once = core._format_banner("", HASH, "Section name", Style())
    text = once + "\n"
    new, changed, errors = core.process_text(text, HASH, Style())
    assert changed == 0
    assert new == text
    assert errors == []


def test_reformats_uneven_widths_to_target():
    text = "# ---------- Setup ---\n"
    new, changed, _ = core.process_text(text, HASH, Style(width=88))
    assert changed == 1
    assert len(new.rstrip("\n")) == 88


def test_detects_one_sided_yolov7_style():
    text = "# Ancillary functions ------------------------------------------\n"
    new, changed, _ = core.process_text(text, HASH, Style(width=88))
    assert changed == 1
    assert new.strip().startswith("# --")
    assert " Ancillary functions " in new


def test_converts_unicode_fill_to_ascii():
    text = "# ——— Loading models ———\n"  # em dashes
    new, changed, _ = core.process_text(text, HASH, Style(width=60))
    assert changed == 1
    assert "—" not in new
    assert new.count("-") > 6


def test_ignores_prose_and_commented_out_code():
    text = '# a normal comment\nx = 1  # trailing note\n# print("=== run ===")\n'
    new, changed, errors = core.process_text(text, HASH, Style())
    assert changed == 0
    assert new == text
    assert errors == []


def test_pure_divider_ignored_by_default_but_normalised_when_enabled():
    text = "# ==============================\n"
    _, changed, _ = core.process_text(text, HASH, Style())
    assert changed == 0
    new, changed_on, _ = core.process_text(text, HASH, Style(dividers=True, width=40))
    assert changed_on == 1
    assert len(new.rstrip("\n")) == 40
    assert set(new.rstrip("\n").removeprefix("# ")) == {"-"}


def test_box_collapses_to_single():
    text = "# ======\n# Setup\n# ======\n"
    new, changed, _ = core.process_text(text, HASH, Style(style="single", width=40))
    assert changed == 1
    assert new.count("\n") == 1
    assert " Setup " in new


def test_single_expands_to_box_and_is_idempotent():
    text = "# --- Setup ---\n"
    boxed, changed, _ = core.process_text(text, HASH, Style(style="box", width=40))
    assert changed == 1
    lines = boxed.splitlines()
    assert len(lines) == 3
    assert lines[1] == "# Setup"
    again, changed_again, _ = core.process_text(boxed, HASH, Style(style="box", width=40))
    assert changed_again == 0
    assert again == boxed


def test_too_long_single_title_errors_and_leaves_unchanged():
    text = "# --- a very long section title indeed ---\n"
    new, changed, errors = core.process_text(text, HASH, Style(width=20))
    assert changed == 0
    assert new == text
    assert len(errors) == 1
    assert "box style" in errors[0]


def test_box_style_accommodates_longer_titles():
    title = "a very long section title indeed"
    text = f"# --- {title} ---\n"
    _, _, errors = core.process_text(text, HASH, Style(style="box", width=60))
    assert errors == []


def test_slash_and_html_syntaxes():
    slash = core._format_banner("", SLASH, "Region", Style(width=40))
    assert slash.startswith("// ") and len(slash) == 40
    html = core._format_banner("", HTML, "Part", Style(width=40))
    assert html.startswith("<!-- ") and html.endswith(" -->") and len(html) == 40


def test_preserves_indentation():
    text = "    # ---- nested ----\n"
    new, changed, _ = core.process_text(text, HASH, Style(width=40))
    assert changed == 1
    assert new.startswith("    # ")
    assert len(new.rstrip("\n")) == 40


def test_preserves_crlf_line_endings():
    text = "# --- Setup ---\r\n"
    new, _, _ = core.process_text(text, HASH, Style(width=40))
    assert new.endswith("\r\n")
    assert "\r" not in new[:-2]


def test_trailing_lone_hash_does_not_leak_into_title():
    text = "# ---------------- model config ---------------- #\n"
    new, changed, _ = core.process_text(text, HASH, Style(width=60))
    assert changed == 1
    assert new.rstrip("\n") == core._format_banner("", HASH, "model config", Style(width=60))


def test_hash_fill_banner_normalises_to_dashes():
    text = "##### basic #####\n"
    new, changed, _ = core.process_text(text, HASH, Style(width=40))
    assert changed == 1
    assert new.rstrip("\n") == core._format_banner("", HASH, "basic", Style(width=40))


def test_fill_glued_to_title_with_trailing_hash():
    text = "# ------------------------hack for training -------------------- #\n"
    new, changed, _ = core.process_text(text, HASH, Style(width=70))
    assert changed == 1
    assert new.rstrip("\n") == core._format_banner("", HASH, "hack for training", Style(width=70))


def test_banner_inside_python_string_is_left_untouched():
    text = 'x = """\n# ==== not a comment ====\nhello\n"""\n'
    protected = core.protected_lines(text, ".py")
    new, changed, errors = core.process_text(text, HASH, Style(width=88), protected=protected)
    assert changed == 0
    assert new == text
    assert errors == []


def test_banner_after_string_still_rewritten():
    text = 'x = """\n# ==== data ====\n"""\n# --- Real section ---\n'
    protected = core.protected_lines(text, ".py")
    new, changed, _ = core.process_text(text, HASH, Style(width=40), protected=protected)
    assert changed == 1
    assert "# ==== data ====" in new  # the in-string line survives verbatim
    assert new.splitlines()[-1] == core._format_banner("", HASH, "Real section", Style(width=40))


def test_unparseable_python_falls_back_to_triple_quote_heuristic():
    text = 'def broken(:\n    x = """\n# ==== nope ====\n"""\n'
    protected = core.protected_lines(text, ".py")
    assert 2 in protected  # the banner-looking line inside the string


def test_backtick_template_literal_protects_banner():
    text = "const s = `\n// ==== not a comment ====\n`;\n"
    protected = core.protected_lines(text, ".ts")
    new, changed, _ = core.process_text(text, SLASH, Style(width=60), protected=protected)
    assert changed == 0
    assert new == text


def test_c_block_comment_banner_normalises_and_stays_block():
    syntaxes = core.syntax_for(".c")
    text = "/* ==== Setup ==== */\n"
    new, changed, _ = core.process_text(text, syntaxes, Style(width=50))
    assert changed == 1
    line = new.rstrip("\n")
    assert line.startswith("/* ") and line.endswith(" */")
    assert len(line) == 50
    assert " Setup " in line


def test_c_family_preserves_each_comment_form():
    syntaxes = core.syntax_for(".ts")
    text = "// --- One ---\n/* --- Two --- */\n"
    new, changed, _ = core.process_text(text, syntaxes, Style(width=40))
    assert changed == 2
    lines = new.splitlines()
    assert lines[0].startswith("// ") and not lines[0].endswith("*/")
    assert lines[1].startswith("/* ") and lines[1].endswith(" */")
    assert len(lines[0]) == 40 and len(lines[1]) == 40


def test_c_block_box_collapses_to_single_block_banner():
    syntaxes = core.syntax_for(".c")
    text = "/* ------ */\n/* Setup */\n/* ------ */\n"
    new, changed, _ = core.process_text(text, syntaxes, Style(style="single", width=40))
    assert changed == 1
    assert new.count("\n") == 1
    assert new.rstrip("\n").startswith("/* ") and new.rstrip("\n").endswith(" */")


def test_left_align_puts_title_first_and_fits_width():
    out = core._format_banner("", HASH, "Setup", Style(width=40, align="left"))
    assert out == "# Setup " + "-" * (40 - len("# Setup "))
    assert len(out) == 40


def test_left_align_is_idempotent():
    style = Style(width=40, align="left")
    once = core._format_banner("", HASH, "Section name", style)
    new, changed, _ = core.process_text(once + "\n", HASH, style)
    assert changed == 0


def test_align_center_is_normalised_from_american_spelling():
    assert Style(align="center").align == "centre"


def test_boxes_disabled_leaves_box_untouched():
    text = "# ======\n# Setup\n# ======\n"
    new, changed, _ = core.process_text(text, HASH, Style(boxes=False, width=40))
    assert changed == 0
    assert new == text


def test_mismatched_rules_around_comment_not_merged():
    text = "# =====================\n# TODO fix this later\n# ---------------------\n"
    new, changed, _ = core.process_text(text, HASH, Style(width=40))
    assert changed == 0
    assert new == text


def test_matching_rules_still_form_a_box():
    text = "# =====================\n# TODO fix this later\n# =====================\n"
    new, changed, _ = core.process_text(text, HASH, Style(style="single", width=40))
    assert changed == 1
    assert new.count("\n") == 1
    assert " TODO fix this later " in new


def test_css_is_block_only_and_ignores_slashes():
    syntaxes = core.syntax_for(".css")
    assert syntaxes == (("/*", "*/"),)
    new, changed, _ = core.process_text("/* ==== Layout ==== */\n", syntaxes, Style(width=40))
    assert changed == 1
    assert new.rstrip("\n").endswith(" */")
    css_slash = "// not a css comment\n"
    unchanged, changed2, _ = core.process_text(css_slash, syntaxes, Style(width=40))
    assert changed2 == 0
    assert unchanged == css_slash
