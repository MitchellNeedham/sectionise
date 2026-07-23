"""Detect and standardise section-header comment banners.

A section header is a comment styled as a banner: a title framed by a run of
fill characters. Real code carries many variants, all of which this module
detects and rewrites to one canonical style:

* single-line, framed both sides:  `# ------- Loading models -------`
* single-line, filled one side:     `# Ancillary functions ----------`
* Unicode fill (em/en dash, rules): `# --- Loading models ---`
* three-line box:                   a rule, a title comment, a rule

Detection is deliberately conservative: only full-line comments whose content
is framed by a fill run of at least `min_run` characters are touched, so
ordinary comments, trailing comments, and commented-out code (for example
`# print("=== x ===")`) are left alone. Title-less rules are ignored unless
`dividers` is enabled.

The pure functions here have no I/O; `cli` wires configuration and files around
them.
"""

import io
import tokenize
from collections.abc import Collection
from dataclasses import dataclass

DEFAULT_WIDTH = 88
DEFAULT_FILL = "-"
# ASCII banner fills plus common Unicode ones (em dash, en dash, box rules).
DEFAULT_DETECT_CHARS = "-=*_~#—–─═"
DEFAULT_MIN_RUN = 3
DEFAULT_STYLE = "single"

# Comment syntax per file extension as (opener, closer). The closer is empty for
# line comments and non-empty for block comments (HTML/XML/Markdown).
_LINE_HASH = ("#", "")
_LINE_SLASH = ("//", "")
_BLOCK_HTML = ("<!--", "-->")

_SYNTAX_BY_SUFFIX = {
    ".py": _LINE_HASH,
    ".pyi": _LINE_HASH,
    ".sh": _LINE_HASH,
    ".bash": _LINE_HASH,
    ".toml": _LINE_HASH,
    ".yaml": _LINE_HASH,
    ".yml": _LINE_HASH,
    ".cfg": _LINE_HASH,
    ".ini": _LINE_HASH,
    ".js": _LINE_SLASH,
    ".jsx": _LINE_SLASH,
    ".ts": _LINE_SLASH,
    ".tsx": _LINE_SLASH,
    ".c": _LINE_SLASH,
    ".h": _LINE_SLASH,
    ".cpp": _LINE_SLASH,
    ".cc": _LINE_SLASH,
    ".java": _LINE_SLASH,
    ".css": _LINE_SLASH,
    ".go": _LINE_SLASH,
    ".rs": _LINE_SLASH,
    ".html": _BLOCK_HTML,
    ".htm": _BLOCK_HTML,
    ".xml": _BLOCK_HTML,
    ".md": _BLOCK_HTML,
}

# Multi-line string delimiters per suffix, used to shield string contents from
# banner rewriting. A line that only looks like a banner because it sits inside
# a here-doc, template literal, or triple-quoted string must be left untouched.
_BACKTICK = ("`", "`")
_PY_TRIPLE = (('"""', '"""'), ("'''", "'''"))
_MULTILINE_STRING_DELIMS = {
    ".js": (_BACKTICK,),
    ".jsx": (_BACKTICK,),
    ".ts": (_BACKTICK,),
    ".tsx": (_BACKTICK,),
    ".go": (_BACKTICK,),
}


@dataclass(frozen=True)
class Style:
    """Banner detection and output settings.

    Attributes:
        width: Target total line length for a rewritten banner or rule.
        fill: The single fill character used in output.
        detect_chars: Characters recognised as banner fill in input.
        min_run: Minimum identical-fill run length to count as a banner side.
        require_both_sides: Only treat a comment as a banner when it has a fill
            run on both sides of the title.
        dividers: Also standardise stand-alone title-less rules.
        style: Output form, `single` (one line) or `box` (three lines).
        max_title: Optional hard cap on title length, on top of the width fit.
    """

    width: int = DEFAULT_WIDTH
    fill: str = DEFAULT_FILL
    detect_chars: str = DEFAULT_DETECT_CHARS
    min_run: int = DEFAULT_MIN_RUN
    require_both_sides: bool = False
    dividers: bool = False
    style: str = DEFAULT_STYLE
    max_title: int | None = None


def syntax_for(suffix: str) -> tuple[str, str] | None:
    """Return the comment syntax for a file suffix, or `None` if unsupported.

    Args:
        suffix: A file extension including the dot (for example `.py`).

    Returns:
        The `(opener, closer)` comment tokens, or `None`.
    """
    return _SYNTAX_BY_SUFFIX.get(suffix.lower())


def _python_protected(text: str) -> set[int] | None:
    """Return 0-based line indices inside Python string tokens, or `None`.

    `None` signals that the source could not be tokenised (for example a syntax
    error), so the caller should fall back to the delimiter heuristic.
    """
    string_types = {tokenize.STRING}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        token_type = getattr(tokenize, name, None)
        if token_type is not None:
            string_types.add(token_type)
    protected: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in string_types and tok.end[0] > tok.start[0]:
                protected.update(range(tok.start[0] - 1, tok.end[0]))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None
    return protected


def _scan_protected(text: str, delims: tuple[tuple[str, str], ...]) -> set[int]:
    """Return 0-based line indices that lie inside a multi-line string.

    A small state machine walks the text tracking whether it is inside one of
    `delims`. Only lines strictly inside a string are marked, so the opening and
    closing lines (which cannot themselves be full-line comments) stay eligible.
    """
    ordered = sorted(delims, key=lambda pair: -len(pair[0]))
    protected: set[int] = set()
    line = 0
    i = 0
    n = len(text)
    closer: str | None = None
    while i < n:
        ch = text[i]
        if ch == "\n":
            if closer is not None:
                protected.add(line)
            line += 1
            i += 1
            continue
        if closer is None:
            for opener, close_token in ordered:
                if text.startswith(opener, i):
                    closer = close_token
                    i += len(opener)
                    break
            else:
                i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if text.startswith(closer, i):
            i += len(closer)
            closer = None
            continue
        i += 1
    return protected


def protected_lines(text: str, suffix: str) -> frozenset[int]:
    """Return 0-based line indices whose content sits inside a string literal.

    Banner rewriting must skip these so a line that only looks like a banner
    because it lives in a docstring, here-doc, or template literal is left
    untouched. Python is tokenised for accuracy; other languages use a
    delimiter heuristic. Languages with no multi-line string form return empty.

    Args:
        text: The full file contents.
        suffix: The file extension including the dot (for example `.py`).

    Returns:
        The set of protected line indices.
    """
    suffix = suffix.lower()
    if suffix in (".py", ".pyi"):
        result = _python_protected(text)
        if result is not None:
            return frozenset(result)
        return frozenset(_scan_protected(text, _PY_TRIPLE))
    delims = _MULTILINE_STRING_DELIMS.get(suffix)
    if delims:
        return frozenset(_scan_protected(text, delims))
    return frozenset()


def _content(line: str) -> str:
    """Return `line` without its trailing newline."""
    return line.rstrip("\r\n")


def _eol(line: str) -> str:
    """Return the trailing newline of a keepends line, or empty at end of file."""
    return line[len(_content(line)) :]


def _extract(content: str, syntax: tuple[str, str]) -> tuple[str, str] | None:
    """Split a full-line comment into its indent and inner text.

    Args:
        content: One line with the newline already removed.
        syntax: The `(opener, closer)` comment tokens.

    Returns:
        `(indent, inner)` when the whole line is a comment in this syntax, else
        `None` (a code line, or a trailing comment).
    """
    opener, closer = syntax
    stripped = content.lstrip()
    indent = content[: len(content) - len(stripped)]
    if not stripped.startswith(opener):
        return None
    inner = stripped[len(opener) :]
    if closer:
        if not inner.rstrip().endswith(closer):
            return None
        inner = inner.rstrip()[: -len(closer)]
    return indent, inner


def _side_runs(inner: str, style: Style) -> tuple[int, int, str]:
    """Measure the leading and trailing fill runs and the title between them.

    Args:
        inner: The comment's inner text (between the comment tokens).
        style: The active settings.

    Returns:
        `(leading_run, trailing_run, title)` for the stripped inner text.
    """
    stripped = inner.strip()
    if not stripped:
        return 0, 0, ""
    lead = 0
    if stripped[0] in style.detect_chars:
        char = stripped[0]
        while lead < len(stripped) and stripped[lead] == char:
            lead += 1
    trail = 0
    if stripped[-1] in style.detect_chars:
        char = stripped[-1]
        while trail < len(stripped) and stripped[-1 - trail] == char:
            trail += 1
    # Strip any residual fill characters left at the title edges, so decoration
    # like a trailing lone `#` (`# --- title --- #`) cannot drag the real fill
    # run into the title.
    title = stripped[lead : len(stripped) - trail].strip(style.detect_chars + " \t")
    return lead, trail, title


def _is_rule(content: str, syntax: tuple[str, str], style: Style) -> bool:
    """Return whether a line is a title-less fill rule."""
    extracted = _extract(content, syntax)
    if extracted is None:
        return False
    lead, trail, title = _side_runs(extracted[1], style)
    return not title and (lead >= style.min_run or trail >= style.min_run)


def _banner_title(content: str, syntax: tuple[str, str], style: Style) -> str | None:
    """Return the title if a line is a single-line banner, else `None`."""
    extracted = _extract(content, syntax)
    if extracted is None:
        return None
    lead, trail, title = _side_runs(extracted[1], style)
    if not title:
        return None
    has_lead = lead >= style.min_run
    has_trail = trail >= style.min_run
    ok = (has_lead and has_trail) if style.require_both_sides else (has_lead or has_trail)
    return title if ok else None


def _box_title(content: str, syntax: tuple[str, str], style: Style) -> str | None:
    """Return the title of a box's middle comment line, else `None`."""
    extracted = _extract(content, syntax)
    if extracted is None:
        return None
    title = _side_runs(extracted[1], style)[2]
    return title or None


def _format_banner(indent: str, syntax: tuple[str, str], title: str, style: Style) -> str:
    """Render a canonical single-line banner: fill padded around a centred title."""
    opener, closer = syntax
    open_part = f"{opener} "
    close_part = f" {closer}" if closer else ""
    fixed = len(indent) + len(open_part) + 1 + len(title) + 1 + len(close_part)
    total = max(style.width - fixed, 2 * style.min_run)
    left = total // 2
    right = total - left
    return f"{indent}{open_part}{style.fill * left} {title} {style.fill * right}{close_part}"


def _format_rule(indent: str, syntax: tuple[str, str], style: Style) -> str:
    """Render a canonical title-less fill rule padded to the target width."""
    opener, closer = syntax
    open_part = f"{opener} "
    close_part = f" {closer}" if closer else ""
    count = max(style.width - len(indent) - len(open_part) - len(close_part), style.min_run)
    return f"{indent}{open_part}{style.fill * count}{close_part}"


def _format_title_line(indent: str, syntax: tuple[str, str], title: str) -> str:
    """Render the middle title line of a box header."""
    opener, closer = syntax
    close_part = f" {closer}" if closer else ""
    return f"{indent}{opener} {title}{close_part}"


def _render(indent: str, syntax: tuple[str, str], title: str, style: Style) -> list[str]:
    """Return the canonical output lines for a banner in the chosen style."""
    if style.style == "box":
        rule = _format_rule(indent, syntax, style)
        return [rule, _format_title_line(indent, syntax, title), rule]
    return [_format_banner(indent, syntax, title, style)]


def _title_limit(indent: str, syntax: tuple[str, str], style: Style) -> int:
    """Return the maximum title length that fits the chosen style and cap."""
    opener, closer = syntax
    open_part = f"{opener} "
    close_part = f" {closer}" if closer else ""
    if style.style == "box":
        fit = style.width - len(indent) - len(open_part) - len(close_part)
    else:
        fit = (
            style.width
            - len(indent)
            - len(open_part)
            - 2  # the two spaces framing the title
            - len(close_part)
            - 2 * style.min_run
        )
    fit = max(fit, 1)
    return min(fit, style.max_title) if style.max_title is not None else fit


def _too_long_error(path: str, lineno: int, title: str, limit: int, style: Style) -> str:
    """Build the error message for an over-long title."""
    base = (
        f"{path}:{lineno}: section title is {len(title)} chars but the limit is "
        f"{limit} ({style.style} style, width {style.width})."
    )
    if style.style == "single":
        return base + " Shorten it, or use box style for a multi-line header."
    return base + " Shorten it."


def _emit_unit(
    lines: list[str],
    start: int,
    end: int,
    indent: str,
    title: str,
    syntax: tuple[str, str],
    style: Style,
    path: str,
    file_eol: str,
) -> tuple[str, bool, str | None]:
    """Render one banner unit, or pass it through on an over-long title.

    Args:
        lines: The file split with line endings kept.
        start: Index of the unit's first source line.
        end: Index of the unit's last source line.
        indent: The unit's leading whitespace.
        title: The extracted title.
        syntax: The `(opener, closer)` comment tokens.
        style: The active settings.
        path: Display path for error messages.
        file_eol: The file's dominant newline, used when a source line has none.

    Returns:
        `(chunk, changed, error)`. On error, `chunk` is the original text so the
        source is left untouched.
    """
    original = "".join(lines[start : end + 1])
    limit = _title_limit(indent, syntax, style)
    if len(title) > limit:
        return original, False, _too_long_error(path, start + 1, title, limit, style)

    rendered = _render(indent, syntax, title, style)
    eol = _eol(lines[start]) or file_eol
    had_final_newline = bool(_eol(lines[end]))
    chunk = ""
    for idx, out_line in enumerate(rendered):
        chunk += out_line
        if idx < len(rendered) - 1 or had_final_newline:
            chunk += eol
    return chunk, chunk != original, None


def process_text(
    text: str,
    syntax: tuple[str, str],
    style: Style,
    path: str = "<text>",
    protected: Collection[int] = (),
) -> tuple[str, int, list[str]]:
    """Reformat every section-header banner in `text`.

    Args:
        text: The full file contents.
        syntax: The `(opener, closer)` comment tokens for the file.
        style: The active settings.
        path: Display path used in error messages.
        protected: 0-based line indices to leave untouched because they sit
            inside a string literal (see `protected_lines`).

    Returns:
        `(new_text, changed_count, errors)`. Passthrough lines keep their exact
        original newline; rewritten units use their first source line's newline.
    """
    lines = text.splitlines(keepends=True)
    n = len(lines)
    file_eol = "\r\n" if "\r\n" in text else "\n"
    protected = frozenset(protected)
    out: list[str] = []
    changed = 0
    errors: list[str] = []

    i = 0
    while i < n:
        if i in protected:
            out.append(lines[i])
            i += 1
            continue

        c0 = _content(lines[i])

        # Three-line box: rule / title comment / rule, all the same indent.
        if i + 2 < n and not (protected & {i + 1, i + 2}):
            c1, c2 = _content(lines[i + 1]), _content(lines[i + 2])
            if (
                _is_rule(c0, syntax, style)
                and not _is_rule(c1, syntax, style)
                and _is_rule(c2, syntax, style)
            ):
                title = _box_title(c1, syntax, style)
                indents = [_extract(c, syntax) for c in (c0, c1, c2)]
                if title and all(indents) and len({e[0] for e in indents}) == 1:
                    chunk, did, error = _emit_unit(
                        lines, i, i + 2, indents[0][0], title, syntax, style, path, file_eol
                    )
                    out.append(chunk)
                    changed += did
                    if error:
                        errors.append(error)
                    i += 3
                    continue

        # Stand-alone title-less rule.
        if _is_rule(c0, syntax, style):
            if style.dividers:
                rule = _format_rule(_extract(c0, syntax)[0], syntax, style)
                new_line = rule + (_eol(lines[i]) or file_eol) if _eol(lines[i]) else rule
                out.append(new_line)
                changed += new_line != lines[i]
            else:
                out.append(lines[i])
            i += 1
            continue

        # Single-line banner.
        title = _banner_title(c0, syntax, style)
        if title:
            chunk, did, error = _emit_unit(
                lines, i, i, _extract(c0, syntax)[0], title, syntax, style, path, file_eol
            )
            out.append(chunk)
            changed += did
            if error:
                errors.append(error)
            i += 1
            continue

        out.append(lines[i])
        i += 1

    return "".join(out), changed, errors
