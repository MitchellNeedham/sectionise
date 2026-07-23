"""Command-line entry point for sectionise.

Resolves settings with the precedence flags > `[tool.sectionise]` in the nearest
`pyproject.toml` > built-in defaults, then lints or autofixes the given files.
Exits non-zero when it changed anything or hit an over-long title, matching the
pre-commit formatter convention.
"""

import argparse
import codecs
import difflib
import os
import sys
import tomllib
from pathlib import Path

from . import __version__, core

_BOM = chr(0xFEFF)  # UTF-8 byte-order mark, kept and re-applied verbatim

# Directories skipped when a directory argument is walked, so vendored and
# generated trees are never rewritten by accident.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".tox",
        ".idea",
        ".eggs",
        "dist",
        "build",
    }
)


def _collect_targets(
    names: list[str], custom_suffixes_of
) -> tuple[list[Path], list[str]]:
    """Expand input names into candidate files, plus warnings for missing ones.

    Directories are walked recursively (skipping vendored and generated trees),
    keeping files whose suffix is a supported built-in or a configured custom
    language. Explicitly named files are always kept; whether they are actually
    processed (by suffix, custom language, or shebang) is decided per file, so
    the caller warns about a truly unsupported one. Names that do not exist warn
    here.

    Args:
        names: The raw filename arguments.
        custom_suffixes_of: Callable mapping a path to the set of custom-language
            suffixes configured for it.

    Returns:
        `(files, warnings)`.
    """
    files: list[Path] = []
    warnings: list[str] = []
    for name in names:
        path = Path(name)
        if path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
                for filename in sorted(filenames):
                    child = Path(dirpath) / filename
                    supported = core.syntax_for(child.suffix) is not None
                    if supported or child.suffix.lower() in custom_suffixes_of(child):
                        files.append(child)
        elif path.is_file():
            files.append(path)
        else:
            warnings.append(f"skipped {name}: not found")
    return files, warnings


def _find_pyproject(start: Path) -> Path | None:
    """Return the nearest `pyproject.toml` at or above `start`, else `None`."""
    start = start.resolve()
    for parent in (start, *start.parents):
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _load_config(path: Path | None) -> dict:
    """Read the `[tool.sectionise]` table from a `pyproject.toml`.

    Args:
        path: The file to read, or `None`.

    Returns:
        The table as a dict, or an empty dict when absent or unreadable.
    """
    if path is None or not path.is_file():
        return {}
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    tool = data.get("tool", {})
    section = tool.get("sectionise", {})
    return section if isinstance(section, dict) else {}


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Overridable options default to `None` so an unset flag falls through to
    `pyproject.toml` and then the built-in default.
    """
    parser = argparse.ArgumentParser(
        prog="sectionise",
        description="Standardise section-header comment banners.",
    )
    parser.add_argument(
        "filenames", nargs="*", help="Files to process, or - for stdin."
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="pyproject.toml to read."
    )
    parser.add_argument(
        "--encoding",
        default=None,
        help="Text encoding to read and write files as (default utf-8).",
    )
    parser.add_argument(
        "--stdin-filename",
        default=None,
        help="Name used to pick the comment syntax when reading stdin (default a .py name).",
    )
    parser.add_argument("--width", type=int, default=None, help="Target line length.")
    parser.add_argument("--fill", default=None, help="Output fill character.")
    parser.add_argument(
        "--detect-chars", default=None, help="Characters recognised as banner fill."
    )
    parser.add_argument(
        "--min-run", type=int, default=None, help="Minimum fill-run length."
    )
    parser.add_argument(
        "--tab-width",
        type=int,
        default=None,
        help="Columns a leading tab occupies (default 8).",
    )
    parser.add_argument(
        "--style", choices=("single", "box"), default=None, help="Output form."
    )
    parser.add_argument(
        "--align",
        choices=("centre", "center", "left"),
        default=None,
        help="Single-line title placement (default centre).",
    )
    parser.add_argument(
        "--max-title", type=int, default=None, help="Hard cap on title length."
    )
    parser.add_argument(
        "--require-both-sides",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Only treat comments with fill on both sides as banners.",
    )
    parser.add_argument(
        "--dividers",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Also standardise stand-alone title-less rules.",
    )
    parser.add_argument(
        "--boxes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Recognise three-line box headers (on by default).",
    )
    parser.add_argument(
        "--check", action="store_true", help="Report only; do not rewrite files."
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Print a unified diff of changes; do not rewrite.",
    )
    return parser


def _resolve_style(
    args: argparse.Namespace,
    global_config: dict,
    lang_config: dict,
    lang_defaults: dict,
) -> core.Style:
    """Merge settings into a `Style` by precedence, highest first.

    Order: CLI flag, per-language `pyproject.toml` table, global `pyproject.toml`
    table, built-in per-language default, built-in global default.

    Args:
        args: Parsed arguments (unset overridable flags are `None`).
        lang_config: The `[tool.sectionise.language.<name>]` table for the file.
        global_config: The `[tool.sectionise]` table.
        lang_defaults: The language's opinionated built-in defaults.
    """

    def pick(flag_value, key, default):
        if flag_value is not None:
            return flag_value
        for layer in (lang_config, global_config, lang_defaults):
            if key in layer:
                return layer[key]
        return default

    return core.Style(
        width=pick(args.width, "width", core.DEFAULT_WIDTH),
        fill=pick(args.fill, "fill", core.DEFAULT_FILL),
        detect_chars=pick(args.detect_chars, "detect_chars", core.DEFAULT_DETECT_CHARS),
        min_run=pick(args.min_run, "min_run", core.DEFAULT_MIN_RUN),
        require_both_sides=pick(args.require_both_sides, "require_both_sides", False),
        dividers=pick(args.dividers, "dividers", False),
        boxes=pick(args.boxes, "boxes", True),
        style=pick(args.style, "style", core.DEFAULT_STYLE),
        align=pick(args.align, "align", core.DEFAULT_ALIGN),
        tab_width=pick(args.tab_width, "tab_width", core.DEFAULT_TAB_WIDTH),
        max_title=pick(args.max_title, "max_title", None),
    )


def _language_config(config: dict, language: str | None) -> dict:
    """Return the `[tool.sectionise.language.<name>]` sub-table for a language."""
    table = config.get("language", {})
    if not isinstance(table, dict) or language is None:
        return {}
    section = table.get(language, {})
    return section if isinstance(section, dict) else {}


def _parse_comments(name: str, spec) -> core.Syntaxes:
    """Turn a custom language's `comments` list into comment syntaxes.

    Each entry is either a string (a line-comment opener like `#`) or a two-item
    list `[opener, closer]` for a block comment.

    Raises:
        ValueError: If an entry is not a string or a valid `[opener, closer]`.
    """
    syntaxes: list[core.Syntax] = []
    for item in spec:
        if isinstance(item, str) and item:
            syntaxes.append((item, ""))
        elif (
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(part, str) and part for part in item)
        ):
            syntaxes.append((item[0], item[1]))
        else:
            raise ValueError(
                f"custom language '{name}': each comment must be a string or "
                f"an [opener, closer] pair, got {item!r}"
            )
    return tuple(syntaxes)


def _custom_languages(config: dict) -> dict[str, dict]:
    """Return custom languages defined in the config.

    A `[tool.sectionise.language.<name>]` table that carries `suffixes` and
    `comments` defines a new language; tables with only settings tune a built-in
    one and are ignored here.

    Returns:
        `{name: {"suffixes": tuple, "syntaxes": Syntaxes}}`.

    Raises:
        ValueError: If a definition is malformed.
    """
    table = config.get("language", {})
    if not isinstance(table, dict):
        return {}
    languages: dict[str, dict] = {}
    for name, settings in table.items():
        if not isinstance(settings, dict) or not (
            "suffixes" in settings or "comments" in settings
        ):
            continue
        suffixes, comments = settings.get("suffixes"), settings.get("comments")
        if not (
            isinstance(suffixes, list)
            and suffixes
            and isinstance(comments, list)
            and comments
        ):
            raise ValueError(
                f"custom language '{name}' needs a non-empty 'suffixes' list and "
                f"'comments' list"
            )
        normalised = []
        for suffix in suffixes:
            if not (isinstance(suffix, str) and suffix.startswith(".")):
                raise ValueError(
                    f"custom language '{name}': suffix {suffix!r} must be a string like '.foo'"
                )
            normalised.append(suffix.lower())
        languages[name] = {
            "suffixes": tuple(normalised),
            "syntaxes": _parse_comments(name, comments),
        }
    return languages


def _resolve_language(
    config: dict, suffix: str, text: str
) -> tuple[str | None, core.Syntaxes | None]:
    """Return `(language, syntaxes)` for a file: built-in, then custom, then shebang."""
    suffix = suffix.lower()
    builtin = core.syntax_for(suffix)
    if builtin is not None:
        return core.language_for(suffix), builtin
    for name, spec in _custom_languages(config).items():
        if suffix in spec["suffixes"]:
            return name, spec["syntaxes"]
    shebang = core.shebang_language(text)
    if shebang is not None:
        return shebang, core.syntaxes_for_language(shebang)
    return None, None


def _resolve_encoding(args: argparse.Namespace, config: dict) -> str:
    """Merge the `--encoding` flag over `pyproject.toml` over the utf-8 default.

    Raises:
        ValueError: If the resolved encoding name is not one Python knows.
    """
    encoding = (
        args.encoding if args.encoding is not None else config.get("encoding", "utf-8")
    )
    try:
        codecs.lookup(encoding)
    except (LookupError, TypeError) as exc:
        raise ValueError(f"unknown encoding {encoding!r}") from exc
    return encoding


def _unified_diff(old: str, new: str, name: str) -> str:
    """Return a unified diff from `old` to `new`, or empty when identical."""
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(), fromfile=name, tofile=name, lineterm=""
    )
    return "\n".join(lines)


def _run_stdin(args: argparse.Namespace, forced_config: dict | None) -> int:
    """Process stdin and write the result (or a diff) to stdout.

    Args:
        args: Parsed arguments.
        forced_config: Config from `--config`, or `None` to look one up.

    Returns:
        `0` clean, `1` changed (or would change), `2` on error.
    """
    stdin_name = args.stdin_filename or "stdin.py"
    suffix = Path(stdin_name).suffix
    syntax = core.syntax_for(suffix)
    if syntax is None:
        print(f"sectionise: unsupported stdin filename {stdin_name!r}", file=sys.stderr)
        return 2
    config = (
        forced_config
        if forced_config is not None
        else _load_config(_find_pyproject(Path.cwd()))
    )
    language = core.language_for(suffix)
    try:
        style = _resolve_style(
            args,
            config,
            _language_config(config, language),
            core.language_defaults(language),
        )
        _resolve_encoding(args, config)
    except ValueError as exc:
        print(f"sectionise: invalid configuration: {exc}", file=sys.stderr)
        return 2

    text = sys.stdin.read()
    protected = core.protected_lines(text, suffix)
    new_text, changed, errors = core.process_text(
        text, syntax, style, stdin_name, protected
    )
    for error in errors:
        print(error, file=sys.stderr)

    if args.diff:
        diff = _unified_diff(text, new_text, stdin_name)
        if diff:
            print(diff)
    elif not args.check:
        sys.stdout.write(new_text)

    if errors:
        return 2
    return 1 if changed else 0


def main(argv: list[str] | None = None) -> int:
    """Lint or autofix section-header banners in the given files.

    Args:
        argv: Argument list; defaults to `sys.argv[1:]`.

    Returns:
        `0` when nothing needs changing, `1` when a file was (or would be)
        reformatted, and `2` when a title was too long to fit. A `2` outranks a
        `1`: a hard error is reported even if other files also changed.
    """
    args = _build_parser().parse_args(argv)
    forced_config = _load_config(args.config) if args.config else None

    if args.filenames == ["-"]:
        return _run_stdin(args, forced_config)

    # Settings come from each file's own nearest pyproject.toml (unless one is
    # forced with --config), so a monorepo's per-package overrides are honoured.
    # Encoding is language-independent and needed before the read, so it is
    # resolved and cached separately from the language-dependent Style.
    config_cache: dict[Path | None, dict] = {}
    encoding_cache: dict[Path | None, str] = {}
    style_cache: dict[tuple, core.Style] = {}

    def config_key_for(path: Path) -> Path | None:
        return (
            args.config if forced_config is not None else _find_pyproject(path.parent)
        )

    def config_for(config_key: Path | None) -> dict:
        if config_key not in config_cache:
            config_cache[config_key] = (
                forced_config if forced_config is not None else _load_config(config_key)
            )
        return config_cache[config_key]

    def encoding_for(config_key: Path | None) -> str:
        if config_key not in encoding_cache:
            encoding_cache[config_key] = _resolve_encoding(args, config_for(config_key))
        return encoding_cache[config_key]

    def style_for(config_key: Path | None, language: str | None) -> core.Style:
        cache_key = (config_key, language)
        if cache_key not in style_cache:
            config = config_for(config_key)
            style_cache[cache_key] = _resolve_style(
                args,
                config,
                _language_config(config, language),
                core.language_defaults(language),
            )
        return style_cache[cache_key]

    def custom_suffixes_of(path: Path) -> set[str]:
        try:
            langs = _custom_languages(config_for(config_key_for(path)))
        except ValueError:
            return set()  # a malformed definition is reported per file in the loop
        return {suffix for spec in langs.values() for suffix in spec["suffixes"]}

    files, warnings = _collect_targets(args.filenames, custom_suffixes_of)
    for warning in warnings:
        print(f"sectionise: {warning}", file=sys.stderr)

    changed_files: list[str] = []
    all_errors: list[str] = []
    for path in files:
        name = str(path)
        config_key = config_key_for(path)
        try:
            encoding = encoding_for(config_key)
        except ValueError as exc:
            all_errors.append(f"invalid configuration for {name}: {exc}")
            continue
        try:
            # newline="" disables universal-newline translation so a file's
            # existing CRLF or LF endings survive the read/write round-trip.
            with open(path, encoding=encoding, newline="") as handle:
                raw = handle.read()
        except UnicodeDecodeError:
            print(
                f"sectionise: skipped {name}: not valid {encoding} text",
                file=sys.stderr,
            )
            continue
        except OSError as exc:
            print(f"sectionise: skipped {name}: {exc.strerror or exc}", file=sys.stderr)
            continue
        bom = raw.startswith(_BOM)
        text = raw[1:] if bom else raw
        try:
            language, syntax = _resolve_language(
                config_for(config_key), path.suffix, text
            )
        except ValueError as exc:
            all_errors.append(f"invalid configuration for {name}: {exc}")
            continue
        if syntax is None:
            print(f"sectionise: skipped {name}: unsupported file type", file=sys.stderr)
            continue
        try:
            style = style_for(config_key, language)
        except ValueError as exc:
            all_errors.append(f"invalid configuration for {name}: {exc}")
            continue
        protect_suffix = (
            path.suffix
            if core.syntax_for(path.suffix)
            else core.primary_suffix(language)
        )
        protected = core.protected_lines(text, protect_suffix)
        new_text, changed, errors = core.process_text(
            text, syntax, style, name, protected
        )
        all_errors.extend(errors)
        if changed:
            changed_files.append(name)
            if args.diff:
                print(_unified_diff(text, new_text, name))
            elif not args.check:
                out_text = _BOM + new_text if bom else new_text
                with open(path, "w", encoding=encoding, newline="") as handle:
                    handle.write(out_text)

    if not args.diff:
        verb = "would reformat" if args.check else "reformatted"
        for name in changed_files:
            print(f"{verb} section headers in {name}")
    for error in dict.fromkeys(all_errors):  # dedupe repeated config errors
        print(error, file=sys.stderr)
    if all_errors:
        return 2
    return 1 if changed_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
