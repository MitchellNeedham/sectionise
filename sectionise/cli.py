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


def _collect_targets(names: list[str]) -> tuple[list[Path], list[str]]:
    """Expand input names into supported files, plus warnings for skipped inputs.

    Directories are walked recursively (skipping vendored and generated trees),
    keeping only files of a supported type. Explicitly named files of an
    unsupported type, and names that do not exist, produce a warning so the user
    is not left thinking an unsupported path was processed.

    Args:
        names: The raw filename arguments.

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
                    if core.syntax_for(child.suffix) is not None:
                        files.append(child)
        elif path.is_file():
            if core.syntax_for(path.suffix) is None:
                warnings.append(f"skipped {name}: unsupported file type")
            else:
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
    parser.add_argument("filenames", nargs="*", help="Files to process, or - for stdin.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="pyproject.toml to read.")
    parser.add_argument(
        "--encoding", default=None, help="Text encoding to read and write files as (default utf-8)."
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
    parser.add_argument("--min-run", type=int, default=None, help="Minimum fill-run length.")
    parser.add_argument(
        "--style", choices=("single", "box"), default=None, help="Output form."
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
        "--diff", action="store_true", help="Print a unified diff of changes; do not rewrite."
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
        max_title=pick(args.max_title, "max_title", None),
    )


def _language_config(config: dict, language: str | None) -> dict:
    """Return the `[tool.sectionise.language.<name>]` sub-table for a language."""
    table = config.get("language", {})
    if not isinstance(table, dict) or language is None:
        return {}
    section = table.get(language, {})
    return section if isinstance(section, dict) else {}


def _resolve_encoding(args: argparse.Namespace, config: dict) -> str:
    """Merge the `--encoding` flag over `pyproject.toml` over the utf-8 default.

    Raises:
        ValueError: If the resolved encoding name is not one Python knows.
    """
    encoding = args.encoding if args.encoding is not None else config.get("encoding", "utf-8")
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
    config = forced_config if forced_config is not None else _load_config(_find_pyproject(Path.cwd()))
    language = core.language_for(suffix)
    try:
        style = _resolve_style(
            args, config, _language_config(config, language), core.language_defaults(language)
        )
        _resolve_encoding(args, config)
    except ValueError as exc:
        print(f"sectionise: invalid configuration: {exc}", file=sys.stderr)
        return 2

    text = sys.stdin.read()
    protected = core.protected_lines(text, suffix)
    new_text, changed, errors = core.process_text(text, syntax, style, stdin_name, protected)
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

    files, warnings = _collect_targets(args.filenames)
    for warning in warnings:
        print(f"sectionise: {warning}", file=sys.stderr)

    # Resolve settings from each file's own nearest pyproject.toml (unless one is
    # forced with --config), so a monorepo's per-package overrides are honoured.
    # Cache by the resolved config path so each pyproject is read once.
    # Cache by (config source, language) since settings vary on both.
    settings_cache: dict[tuple, tuple[core.Style, str]] = {}

    def resolve_for(path: Path) -> tuple[core.Style, str]:
        config_key = args.config if forced_config is not None else _find_pyproject(path.parent)
        language = core.language_for(path.suffix)
        cache_key = (config_key, language)
        if cache_key not in settings_cache:
            config = forced_config if forced_config is not None else _load_config(config_key)
            style = _resolve_style(
                args, config, _language_config(config, language), core.language_defaults(language)
            )
            settings_cache[cache_key] = (style, _resolve_encoding(args, config))
        return settings_cache[cache_key]

    changed_files: list[str] = []
    all_errors: list[str] = []
    for path in files:
        name = str(path)
        syntax = core.syntax_for(path.suffix)
        try:
            style, encoding = resolve_for(path)
        except ValueError as exc:
            all_errors.append(f"invalid configuration for {name}: {exc}")
            continue
        try:
            # newline="" disables universal-newline translation so a file's
            # existing CRLF or LF endings survive the read/write round-trip.
            with open(path, encoding=encoding, newline="") as handle:
                raw = handle.read()
        except UnicodeDecodeError:
            print(f"sectionise: skipped {name}: not valid {encoding} text", file=sys.stderr)
            continue
        except OSError as exc:
            print(f"sectionise: skipped {name}: {exc.strerror or exc}", file=sys.stderr)
            continue
        bom = raw.startswith(_BOM)
        text = raw[1:] if bom else raw
        protected = core.protected_lines(text, path.suffix)
        new_text, changed, errors = core.process_text(text, syntax, style, name, protected)
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
