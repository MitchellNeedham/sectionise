"""Command-line entry point for sectionise.

Resolves settings with the precedence flags > `[tool.sectionise]` in the nearest
`pyproject.toml` > built-in defaults, then lints or autofixes the given files.
Exits non-zero when it changed anything or hit an over-long title, matching the
pre-commit formatter convention.
"""

import argparse
import tomllib
from pathlib import Path

from . import core


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
    parser.add_argument("filenames", nargs="*", help="Files to process.")
    parser.add_argument("--config", type=Path, default=None, help="pyproject.toml to read.")
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
    return parser


def _resolve_style(args: argparse.Namespace, config: dict) -> core.Style:
    """Merge flags over `pyproject.toml` over defaults into a `Style`."""

    def pick(flag_value, key, default):
        if flag_value is not None:
            return flag_value
        return config.get(key, default)

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


def main(argv: list[str] | None = None) -> int:
    """Lint or autofix section-header banners in the given files.

    Args:
        argv: Argument list; defaults to `sys.argv[1:]`.

    Returns:
        `1` if any file was (or would be) changed or a title was too long, else
        `0`.
    """
    args = _build_parser().parse_args(argv)
    config = _load_config(args.config or _find_pyproject(Path.cwd()))
    style = _resolve_style(args, config)

    changed_files: list[str] = []
    all_errors: list[str] = []
    for name in args.filenames:
        path = Path(name)
        syntax = core.syntax_for(path.suffix)
        if syntax is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        protected = core.protected_lines(text, path.suffix)
        new_text, changed, errors = core.process_text(text, syntax, style, name, protected)
        all_errors.extend(errors)
        if changed:
            changed_files.append(name)
            if not args.check:
                path.write_text(new_text, encoding="utf-8")

    verb = "would reformat" if args.check else "reformatted"
    for name in changed_files:
        print(f"{verb} section headers in {name}")
    for error in all_errors:
        print(error)
    return 1 if changed_files or all_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
