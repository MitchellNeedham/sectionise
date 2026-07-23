"""Command-line entry point for sectionise.

Resolves settings with the precedence flags > `[tool.sectionise]` in the nearest
`pyproject.toml` > built-in defaults, then lints or autofixes the given files.
Exits non-zero when it changed anything or hit an over-long title, matching the
pre-commit formatter convention.
"""

import argparse
import os
import sys
import tomllib
from pathlib import Path

from . import core

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
    forced_config = _load_config(args.config) if args.config else None

    files, warnings = _collect_targets(args.filenames)
    for warning in warnings:
        print(f"sectionise: {warning}", file=sys.stderr)

    # Resolve settings from each file's own nearest pyproject.toml (unless one is
    # forced with --config), so a monorepo's per-package overrides are honoured.
    # Cache by the resolved config path so each pyproject is read once.
    style_cache: dict[Path | None, core.Style] = {}

    def resolve_for(path: Path) -> core.Style:
        key = args.config if forced_config is not None else _find_pyproject(path.parent)
        if key not in style_cache:
            config = forced_config if forced_config is not None else _load_config(key)
            style_cache[key] = _resolve_style(args, config)
        return style_cache[key]

    changed_files: list[str] = []
    all_errors: list[str] = []
    for path in files:
        name = str(path)
        syntax = core.syntax_for(path.suffix)
        style = resolve_for(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"sectionise: skipped {name}: not valid UTF-8 text", file=sys.stderr)
            continue
        except OSError as exc:
            print(f"sectionise: skipped {name}: {exc.strerror or exc}", file=sys.stderr)
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
