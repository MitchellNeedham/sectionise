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
DEFAULT_ALIGN = "centre"
DEFAULT_FILL_MODE = "width"
DEFAULT_FILL_COUNT = 3
DEFAULT_TAB_WIDTH = 8

# Comment syntax as (opener, closer). The closer is empty for line comments and
# non-empty for block comments. A language maps to a tuple of syntaxes; a banner
# is detected under any of them and re-rendered in whichever one it was written.
_LINE_HASH = ("#", "")
_LINE_SLASH = ("//", "")
_LINE_DASH = ("--", "")
_BLOCK_HTML = ("<!--", "-->")
_BLOCK_C = ("/*", "*/")

# C-family accepts both `//` line and `/* */` block banners. CSS has no `//`
# comment form at all, so it is block only; SCSS/LESS add the `//` form back.
_CFAMILY = (_LINE_SLASH, _BLOCK_C)
_CSS = (_BLOCK_C,)
_SCSS = (_LINE_SLASH, _BLOCK_C)
_HCL = (_LINE_HASH, _LINE_SLASH, _BLOCK_C)
_SQL = (_LINE_DASH, _BLOCK_C)

_BACKTICK = ("`", "`")
_PY_TRIPLE = (('"""', '"""'), ("'''", "'''"))

# The single source of truth for language support: canonical name, file
# suffixes, comment syntaxes, multi-line string delimiters (to shield string
# contents from rewriting), and opinionated default settings that reflect the
# language's dominant formatter or style guide. Everything below is derived from
# this table. Widths: Black 88 (Python), Prettier 80 (web), rustfmt/Google 100,
# Microsoft/SwiftLint/RuboCop 120.
_LANGUAGES: tuple[tuple[str, tuple[str, ...], "Syntaxes", tuple, dict], ...] = (
    ("python", (".py", ".pyi"), (_LINE_HASH,), _PY_TRIPLE, {"width": 88}),
    ("shell", (".sh", ".bash"), (_LINE_HASH,), (), {"width": 80}),
    ("toml", (".toml",), (_LINE_HASH,), (), {"width": 80}),
    ("yaml", (".yaml", ".yml"), (_LINE_HASH,), (), {"width": 80}),
    ("ini", (".ini", ".cfg"), (_LINE_HASH,), (), {"width": 80}),
    ("ruby", (".rb",), (_LINE_HASH,), (), {"width": 120}),
    ("perl", (".pl", ".pm"), (_LINE_HASH,), (), {"width": 80}),
    ("r", (".r",), (_LINE_HASH,), (), {"width": 80}),
    (
        "javascript",
        (".js", ".jsx", ".mjs", ".cjs"),
        _CFAMILY,
        (_BACKTICK,),
        {"width": 80},
    ),
    ("typescript", (".ts", ".tsx"), _CFAMILY, (_BACKTICK,), {"width": 80}),
    ("c", (".c", ".h"), _CFAMILY, (), {"width": 80}),
    ("cpp", (".cpp", ".cc", ".cxx", ".hpp", ".hh"), _CFAMILY, (), {"width": 80}),
    ("csharp", (".cs",), _CFAMILY, (), {"width": 120}),
    ("java", (".java",), _CFAMILY, (), {"width": 100}),
    ("kotlin", (".kt", ".kts"), _CFAMILY, (), {"width": 100}),
    ("swift", (".swift",), _CFAMILY, (), {"width": 120}),
    ("scala", (".scala",), _CFAMILY, (), {"width": 80}),
    ("dart", (".dart",), _CFAMILY, (), {"width": 80}),
    ("go", (".go",), _CFAMILY, (_BACKTICK,), {"width": 100}),
    ("rust", (".rs",), _CFAMILY, (), {"width": 100}),
    ("css", (".css",), _CSS, (), {"width": 80}),
    ("scss", (".scss", ".less"), _SCSS, (), {"width": 80}),
    ("sql", (".sql",), _SQL, (), {"width": 88}),
    ("lua", (".lua",), (_LINE_DASH,), (), {"width": 80}),
    ("terraform", (".tf",), _HCL, (), {"width": 80}),
    ("html", (".html", ".htm"), (_BLOCK_HTML,), (), {"width": 80}),
    ("xml", (".xml",), (_BLOCK_HTML,), (), {"width": 80}),
    ("markdown", (".md",), (_BLOCK_HTML,), (), {"width": 80}),
)

_SYNTAX_BY_SUFFIX = {sfx: syn for _, sfxs, syn, _, _ in _LANGUAGES for sfx in sfxs}
_LANG_BY_SUFFIX = {sfx: name for name, sfxs, _, _, _ in _LANGUAGES for sfx in sfxs}
_STRING_DELIMS_BY_SUFFIX = {
    sfx: d for _, sfxs, _, d, _ in _LANGUAGES for sfx in sfxs if d
}
_DEFAULTS_BY_LANGUAGE = {name: dict(defaults) for name, _, _, _, defaults in _LANGUAGES}
_SUFFIXES_BY_LANGUAGE = {name: sfxs for name, sfxs, _, _, _ in _LANGUAGES}
_SYNTAXES_BY_LANGUAGE = {name: syn for name, _, syn, _, _ in _LANGUAGES}
_PYTHON_SUFFIXES = frozenset(_SUFFIXES_BY_LANGUAGE["python"])

# Interpreter basename (from a shebang line) to language, so extensionless
# scripts can still be recognised. Version suffixes like `python3.12` are matched
# by prefix.
_SHEBANG_LANGUAGES = {
    "python": "python",
    "bash": "shell",
    "sh": "shell",
    "zsh": "shell",
    "dash": "shell",
    "ruby": "ruby",
    "perl": "perl",
    "node": "javascript",
    "lua": "lua",
    "rscript": "r",
}


@dataclass(frozen=True)
class Style:
    """Banner detection and output settings.

    Attributes:
        width: Target total line length for a rewritten banner or rule.
        fill: The fill used in output. Usually one character; a multi-character
            fill is tiled and truncated to the exact width (`-=` gives
            `-=-=-=-`), and each of its characters is added to `detect_chars`.
        detect_chars: Characters recognised as banner fill in input.
        min_run: Minimum identical-fill run length to count as a banner side.
        require_both_sides: Only treat a comment as a banner when it has a fill
            run on both sides of the title.
        dividers: Also standardise stand-alone title-less rules.
        boxes: Recognise the three-line box form (rule / title / rule) as a
            banner. When off, box-shaped headers are left untouched.
        style: Output form, `single` (one line) or `box` (three lines).
        align: Single-line title placement, `centre` (fill both sides) or `left`
            (title first, fill trailing). The American spelling `center` is
            accepted and normalised to `centre`.
        fill_mode: How much fill a single-line banner carries, `width` (pad to
            the target `width`) or `fixed` (`fill_count` characters per run, so
            the line length grows with the title). `align` still decides the
            sides: `centre` fills both, `left` fills only after the title. Box
            style and stand-alone dividers are unaffected and always span
            `width`.
        fill_count: Fill characters per run when `fill_mode` is `fixed`. Must be
            at least `min_run`, so a rendered banner is still recognised as one.
        bookend: Close a single-line banner with a mirror of the comment opener,
            so the line ends the way it starts (`# --- Title --- #`,
            `// --- Title --- //`). Line comments only; block comments already
            close with their own token, and box style is unaffected. A trailing
            opener is always recognised on input regardless of this setting.
        tab_width: Columns a leading tab occupies, so tab-indented banners reach
            the target width.
        max_title: Optional hard cap on title length, on top of the width fit.
    """

    width: int = DEFAULT_WIDTH
    fill: str = DEFAULT_FILL
    detect_chars: str = DEFAULT_DETECT_CHARS
    min_run: int = DEFAULT_MIN_RUN
    require_both_sides: bool = False
    dividers: bool = False
    boxes: bool = True
    style: str = DEFAULT_STYLE
    align: str = DEFAULT_ALIGN
    fill_mode: str = DEFAULT_FILL_MODE
    fill_count: int = DEFAULT_FILL_COUNT
    bookend: bool = False
    tab_width: int = DEFAULT_TAB_WIDTH
    max_title: int | None = None

    def __post_init__(self) -> None:
        """Validate settings and ensure the fill character is also detectable.

        Raises:
            ValueError: If any setting is out of range or the wrong type. The
                message names the offending setting so a bad `pyproject.toml` is
                easy to fix.
        """
        if (
            not isinstance(self.width, int)
            or isinstance(self.width, bool)
            or self.width < 1
        ):
            raise ValueError(f"width must be a positive integer, got {self.width!r}")
        if (
            not isinstance(self.min_run, int)
            or isinstance(self.min_run, bool)
            or self.min_run < 1
        ):
            raise ValueError(
                f"min_run must be a positive integer, got {self.min_run!r}"
            )
        if (
            not isinstance(self.fill, str)
            or not self.fill
            or any(char.isspace() for char in self.fill)
        ):
            raise ValueError(
                f"fill must be a non-empty string without whitespace, got {self.fill!r}"
            )
        if not isinstance(self.detect_chars, str) or not self.detect_chars:
            raise ValueError(
                f"detect_chars must be a non-empty string, got {self.detect_chars!r}"
            )
        if self.style not in ("single", "box"):
            raise ValueError(f"style must be 'single' or 'box', got {self.style!r}")
        if (
            not isinstance(self.tab_width, int)
            or isinstance(self.tab_width, bool)
            or self.tab_width < 1
        ):
            raise ValueError(
                f"tab_width must be a positive integer, got {self.tab_width!r}"
            )
        if self.align == "center":
            object.__setattr__(self, "align", "centre")
        if self.align not in ("centre", "left"):
            raise ValueError(f"align must be 'centre' or 'left', got {self.align!r}")
        if self.fill_mode not in ("width", "fixed"):
            raise ValueError(
                f"fill_mode must be 'width' or 'fixed', got {self.fill_mode!r}"
            )
        if (
            not isinstance(self.fill_count, int)
            or isinstance(self.fill_count, bool)
            or self.fill_count < 1
        ):
            raise ValueError(
                f"fill_count must be a positive integer, got {self.fill_count!r}"
            )
        # A fixed run shorter than min_run would not be re-detected as a banner,
        # so a freshly written header could not be re-fitted.
        if self.fill_mode == "fixed" and self.fill_count < self.min_run:
            raise ValueError(
                f"fill_count must be at least min_run ({self.min_run}) in fixed "
                f"mode, got {self.fill_count}"
            )
        if self.max_title is not None and (
            not isinstance(self.max_title, int)
            or isinstance(self.max_title, bool)
            or self.max_title < 1
        ):
            raise ValueError(
                f"max_title must be a positive integer or unset, got {self.max_title!r}"
            )
        # Every output fill character must be recognised on the next run, else a
        # freshly written banner would not be seen as one and could not be
        # re-fitted. A multi-character fill seeds each of its characters.
        missing = "".join(
            dict.fromkeys(c for c in self.fill if c not in self.detect_chars)
        )
        if missing:
            object.__setattr__(self, "detect_chars", self.detect_chars + missing)


Syntax = tuple[str, str]
Syntaxes = tuple[Syntax, ...]


def syntax_for(suffix: str) -> Syntaxes | None:
    """Return the comment syntaxes for a file suffix, or `None` if unsupported.

    Args:
        suffix: A file extension including the dot (for example `.py`).

    Returns:
        A tuple of `(opener, closer)` comment tokens (one per recognised comment
        form for the language), or `None`.
    """
    return _SYNTAX_BY_SUFFIX.get(suffix.lower())


def language_for(suffix: str) -> str | None:
    """Return the canonical language name for a file suffix, or `None`.

    Args:
        suffix: A file extension including the dot (for example `.py`).

    Returns:
        The language name (for example `python`), or `None` if unsupported.
    """
    return _LANG_BY_SUFFIX.get(suffix.lower())


def language_defaults(name: str | None) -> dict:
    """Return the built-in default settings for a language name.

    Args:
        name: A canonical language name, as returned by `language_for`.

    Returns:
        A fresh dict of the language's opinionated defaults (empty if unknown or
        `None`). These sit below any user or flag settings in precedence.
    """
    return dict(_DEFAULTS_BY_LANGUAGE.get(name, {}))


def syntaxes_for_language(name: str | None) -> Syntaxes | None:
    """Return the comment syntaxes for a language name, or `None` if unknown."""
    return _SYNTAXES_BY_LANGUAGE.get(name)


def primary_suffix(name: str | None) -> str:
    """Return a representative file suffix for a language, or empty if unknown.

    Used to drive string-literal protection for a file identified by shebang
    rather than extension (for example an extensionless Python script).
    """
    suffixes = _SUFFIXES_BY_LANGUAGE.get(name)
    return suffixes[0] if suffixes else ""


def shebang_language(text: str) -> str | None:
    """Return the language named by a leading `#!` shebang line, else `None`.

    The interpreter basename is matched by prefix so `python3`, `python3.12`,
    and a bare `python` all resolve to Python.

    Args:
        text: The file contents (only the first line is inspected).

    Returns:
        A canonical language name, or `None`.
    """
    if not text.startswith("#!"):
        return None
    first_line = text.splitlines()[0] if text else ""
    tokens = first_line[2:].split()
    if not tokens:
        return None
    # `#!/usr/bin/env python3` -> take the arg after env; else the interpreter.
    interpreter = tokens[0].rsplit("/", 1)[-1]
    if interpreter == "env" and len(tokens) > 1:
        interpreter = tokens[1].rsplit("/", 1)[-1]
    for prefix, language in _SHEBANG_LANGUAGES.items():
        if interpreter.startswith(prefix):
            return language
    return None


def _as_syntaxes(syntax: Syntax | Syntaxes) -> Syntaxes:
    """Normalise a single `(opener, closer)` or a tuple of them into a tuple.

    Accepts either form so callers (and tests) can pass one syntax directly.
    """
    if len(syntax) == 2 and isinstance(syntax[0], str):
        return (syntax,)  # a lone (opener, closer)
    return tuple(syntax)


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
    if suffix in _PYTHON_SUFFIXES:
        result = _python_protected(text)
        if result is not None:
            return frozenset(result)
        return frozenset(_scan_protected(text, _PY_TRIPLE))
    delims = _STRING_DELIMS_BY_SUFFIX.get(suffix)
    if delims:
        return frozenset(_scan_protected(text, delims))
    return frozenset()


def _dominant_eol(text: str) -> str:
    r"""Return the file's prevailing line ending, `\r\n` or `\n`.

    Used only as a fallback for a final line that has no newline of its own, so
    a lone stray `\r\n` in a mostly-`\n` file does not flip the choice.
    """
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _indent_width(indent: str, tab_width: int) -> int:
    """Return the display column count of an indent, expanding tabs to stops."""
    col = 0
    for char in indent:
        col += tab_width - (col % tab_width) if char == "\t" else 1
    return col


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
    else:
        # A line-comment banner may be book-ended with a mirror of the opener
        # (`# --- x --- #`, `// --- x --- //`). Drop a stand-alone trailing
        # opener so the title is recovered, whatever the `bookend` setting is,
        # since the opener may not be a detectable fill character.
        trimmed = inner.rstrip()
        if trimmed.endswith(opener) and (
            len(trimmed) == len(opener) or trimmed[-len(opener) - 1].isspace()
        ):
            inner = trimmed[: -len(opener)]
    return indent, inner


def _strip_edge_fill(text: str, detect_chars: str) -> str:
    """Drop whitespace-separated fill runs from each edge of `text`.

    A run of a fill character that stands alone, bounded by whitespace or the
    string edge, is decoration and is removed, so `title --- #` reduces to
    `title`. A fill character joined to the title text with no space, as in
    `_function_name` or `#Nice`, is part of the title and kept.
    """
    s = text.strip()
    while s and s[0] in detect_chars:
        run = len(s) - len(s.lstrip(s[0]))
        if run < len(s) and not s[run].isspace():
            break  # attached to the title text, so keep it
        s = s[run:].lstrip()
    while s and s[-1] in detect_chars:
        run = len(s) - len(s.rstrip(s[-1]))
        if run < len(s) and not s[-1 - run].isspace():
            break  # attached to the title text, so keep it
        s = s[: len(s) - run].rstrip()
    return s


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
    # A fill run is any maximal run of detect characters, not just one repeated
    # character, so a multi-character fill motif (`-=-=-=`) reads as a single
    # run and the tool's own output round-trips.
    lead = 0
    while lead < len(stripped) and stripped[lead] in style.detect_chars:
        lead += 1
    trail = 0
    while trail < len(stripped) and stripped[-1 - trail] in style.detect_chars:
        trail += 1
    title = _strip_edge_fill(stripped[lead : len(stripped) - trail], style.detect_chars)
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
    ok = (
        (has_lead and has_trail)
        if style.require_both_sides
        else (has_lead or has_trail)
    )
    return title if ok else None


def _box_title(content: str, syntax: tuple[str, str], style: Style) -> str | None:
    """Return the title of a box's middle comment line, else `None`."""
    extracted = _extract(content, syntax)
    if extracted is None:
        return None
    title = _side_runs(extracted[1], style)[2]
    return title or None


def _match_banner(
    content: str, syntaxes: Syntaxes, style: Style
) -> tuple[str, Syntax] | None:
    """Return `(title, syntax)` for the first syntax that reads as a banner."""
    for syntax in syntaxes:
        title = _banner_title(content, syntax, style)
        if title is not None:
            return title, syntax
    return None


def _match_rule(content: str, syntaxes: Syntaxes, style: Style) -> Syntax | None:
    """Return the first syntax under which `content` is a title-less rule."""
    for syntax in syntaxes:
        if _is_rule(content, syntax, style):
            return syntax
    return None


def _rule_char(content: str, syntax: Syntax, style: Style) -> str | None:
    """Return the fill character of a title-less rule line, else `None`."""
    extracted = _extract(content, syntax)
    if extracted is None:
        return None
    stripped = extracted[1].strip()
    return stripped[0] if stripped and stripped[0] in style.detect_chars else None


def _match_box(
    lines: list[str], i: int, syntaxes: Syntaxes, style: Style
) -> tuple[str, str, Syntax] | None:
    """Return `(indent, title, syntax)` if lines `i..i+2` form a three-line box.

    A box is a rule, a title comment, and a rule, all in the same comment syntax
    and indent. The two rules must share a fill character, so two unrelated
    dividers happening to bracket an ordinary comment are not merged into a
    header. The caller guarantees `i + 2` is in range.
    """
    c0, c1, c2 = (_content(lines[i + offset]) for offset in range(3))
    for syntax in syntaxes:
        if not (
            _is_rule(c0, syntax, style)
            and not _is_rule(c1, syntax, style)
            and _is_rule(c2, syntax, style)
        ):
            continue
        if _rule_char(c0, syntax, style) != _rule_char(c2, syntax, style):
            continue
        title = _box_title(c1, syntax, style)
        indents = [_extract(c, syntax) for c in (c0, c1, c2)]
        if title and all(indents) and len({e[0] for e in indents}) == 1:
            return indents[0][0], title, syntax
    return None


def _close_part(syntax: tuple[str, str], style: Style) -> str:
    """Return the trailing part of a single-line banner (space plus closer).

    A block comment uses its own closer. A line comment has none, but closes
    with a mirror of the opener when `bookend` is on, and is otherwise bare.
    """
    opener, closer = syntax
    if closer:
        return f" {closer}"
    return f" {opener}" if style.bookend else ""


def _tile(fill: str, count: int) -> str:
    """Return exactly `count` characters of the fill, tiling and truncating.

    For a single-character fill this is just repetition; a multi-character fill
    is repeated and cut to length, so `-=` at width 7 gives `-=-=-=-`.
    """
    if count <= 0:
        return ""
    return (fill * (count // len(fill) + 1))[:count]


def _format_banner(
    indent: str, syntax: tuple[str, str], title: str, style: Style
) -> str:
    """Render a canonical single-line banner, centred or left-aligned."""
    open_part = f"{syntax[0]} "
    close_part = _close_part(syntax, style)
    if style.fill_mode == "fixed":
        run = _tile(style.fill, style.fill_count)
        if style.align == "left":
            return f"{indent}{open_part}{title} {run}{close_part}"
        return f"{indent}{open_part}{run} {title} {run}{close_part}"
    indent_cols = _indent_width(indent, style.tab_width)
    if style.align == "left":
        used = indent_cols + len(open_part) + len(title) + 1 + len(close_part)
        count = max(style.width - used, style.min_run)
        return f"{indent}{open_part}{title} {_tile(style.fill, count)}{close_part}"
    fixed = indent_cols + len(open_part) + 1 + len(title) + 1 + len(close_part)
    total = max(style.width - fixed, 2 * style.min_run)
    left = total // 2
    right = total - left
    return (
        f"{indent}{open_part}{_tile(style.fill, left)} {title} "
        f"{_tile(style.fill, right)}{close_part}"
    )


def _format_rule(indent: str, syntax: tuple[str, str], style: Style) -> str:
    """Render a canonical title-less fill rule padded to the target width."""
    opener, closer = syntax
    open_part = f"{opener} "
    close_part = f" {closer}" if closer else ""
    indent_cols = _indent_width(indent, style.tab_width)
    count = max(
        style.width - indent_cols - len(open_part) - len(close_part), style.min_run
    )
    return f"{indent}{open_part}{_tile(style.fill, count)}{close_part}"


def _format_title_line(indent: str, syntax: tuple[str, str], title: str) -> str:
    """Render the middle title line of a box header."""
    opener, closer = syntax
    close_part = f" {closer}" if closer else ""
    return f"{indent}{opener} {title}{close_part}"


def _render(
    indent: str, syntax: tuple[str, str], title: str, style: Style
) -> list[str]:
    """Return the canonical output lines for a banner in the chosen style."""
    if style.style == "box":
        rule = _format_rule(indent, syntax, style)
        return [rule, _format_title_line(indent, syntax, title), rule]
    return [_format_banner(indent, syntax, title, style)]


def _title_limit(indent: str, syntax: tuple[str, str], style: Style) -> int | None:
    """Return the maximum title length for the style and cap, or `None`.

    `None` means the title has no length limit, which is the case for a
    fixed-fill single-line banner: its fill runs are a constant length, so the
    line grows with the title and `width` imposes no bound. Only an explicit
    `max_title` caps it.
    """
    opener, closer = syntax
    open_part = f"{opener} "
    if style.style != "box" and style.fill_mode == "fixed":
        return style.max_title
    indent_cols = _indent_width(indent, style.tab_width)
    if style.style == "box":
        # Box is unaffected by bookend, so only its own block closer counts.
        box_close = f" {closer}" if closer else ""
        fit = style.width - indent_cols - len(open_part) - len(box_close)
    elif style.align == "left":
        fit = (
            style.width
            - indent_cols
            - len(open_part)
            - 1  # the space between the title and its trailing fill
            - len(_close_part(syntax, style))
            - style.min_run
        )
    else:
        fit = (
            style.width
            - indent_cols
            - len(open_part)
            - 2  # the two spaces framing the title
            - len(_close_part(syntax, style))
            - 2 * style.min_run
        )
    fit = max(fit, 1)
    return min(fit, style.max_title) if style.max_title is not None else fit


def _too_long_error(
    path: str, lineno: int, title: str, limit: int, style: Style
) -> str:
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
    if limit is not None and len(title) > limit:
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
    syntax: Syntax | Syntaxes,
    style: Style,
    path: str = "<text>",
    protected: Collection[int] = (),
) -> tuple[str, int, list[str]]:
    """Reformat every section-header banner in `text`.

    Args:
        text: The full file contents.
        syntax: A single `(opener, closer)` or a tuple of them; a banner is
            detected under any and re-rendered in whichever one it was written.
        style: The active settings.
        path: Display path used in error messages.
        protected: 0-based line indices to leave untouched because they sit
            inside a string literal (see `protected_lines`).

    Returns:
        `(new_text, changed_count, errors)`. Passthrough lines keep their exact
        original newline; rewritten units use their first source line's newline.
    """
    syntaxes = _as_syntaxes(syntax)
    lines = text.splitlines(keepends=True)
    n = len(lines)
    file_eol = _dominant_eol(text)
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
        if style.boxes and i + 2 < n and not (protected & {i + 1, i + 2}):
            box = _match_box(lines, i, syntaxes, style)
            if box is not None:
                indent, title, matched = box
                chunk, did, error = _emit_unit(
                    lines, i, i + 2, indent, title, matched, style, path, file_eol
                )
                out.append(chunk)
                changed += did
                if error:
                    errors.append(error)
                i += 3
                continue

        # Stand-alone title-less rule.
        rule_syntax = _match_rule(c0, syntaxes, style)
        if rule_syntax is not None:
            if style.dividers:
                rule = _format_rule(_extract(c0, rule_syntax)[0], rule_syntax, style)
                new_line = (
                    rule + (_eol(lines[i]) or file_eol) if _eol(lines[i]) else rule
                )
                out.append(new_line)
                changed += new_line != lines[i]
            else:
                out.append(lines[i])
            i += 1
            continue

        # Single-line banner.
        match = _match_banner(c0, syntaxes, style)
        if match is not None:
            title, matched = match
            chunk, did, error = _emit_unit(
                lines,
                i,
                i,
                _extract(c0, matched)[0],
                title,
                matched,
                style,
                path,
                file_eol,
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
