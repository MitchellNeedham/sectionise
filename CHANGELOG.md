# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Block-comment banners (`/* ===== Section ===== */`) are detected and rendered
  for C-family languages and CSS, preserving whichever comment form a banner
  was written in.
- Built-in support for many more languages, each with an opinionated default
  width matching its dominant formatter or style guide.
- Per-language settings via `[tool.sectionise.language.<name>]`, and
  user-defined custom languages via the same table with `suffixes` and
  `comments`.
- Shebang detection, so extensionless scripts are recognised by their `#!` line.
- New options: `--align` (`centre` or `left`), `--tab-width`, `--encoding`,
  `--diff`, `--version`, `--stdin-filename`, and `--boxes` / `--no-boxes`.
- Reading from stdin and writing to stdout with `-`.
- Directory arguments are expanded recursively, skipping vendored and generated
  trees such as `.venv` and `node_modules`.
- Configuration values are validated with clear, actionable error messages.
- Exit code `2` for hard errors, distinct from `1` for a reformatted file.

### Fixed

- Banner-looking lines inside string literals (docstrings, here-docs, template
  literals) are no longer rewritten. Python is tokenised for accuracy.
- `.css` is treated as block-comment only; CSS has no `//` line comment.
- Line endings are preserved on write; files are no longer converted between LF
  and CRLF.
- A UTF-8 byte-order mark is preserved.
- Settings are resolved from each file's own nearest `pyproject.toml`, so
  monorepo per-package overrides are honoured.
- Errors are written to stderr rather than stdout.
- Tab-indented banners are measured in display columns so they reach the target
  width.
- Skipped inputs (unsupported type, unreadable, not found) are reported rather
  than dropped silently.

### Changed

- Three-line box detection now requires the two rule lines to share a fill
  character, and can be turned off with `boxes = false`.
- The output fill character is always added to `detect_chars`, so a freshly
  written banner is recognised on the next run.

## [0.1.0]

### Added

- Initial release: detect and standardise section-header comment banners as a
  pre-commit hook or CLI, configured from `pyproject.toml`.
