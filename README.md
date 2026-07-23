# sectionise

[![PyPI](https://img.shields.io/pypi/v/sectionise.svg)](https://pypi.org/project/sectionise/)
[![Python](https://img.shields.io/pypi/pyversions/sectionise.svg)](https://pypi.org/project/sectionise/)

Standardise section-header comment banners across a codebase to one canonical
style. Runs as a pre-commit hook or a CLI, and is configured from
`pyproject.toml` (with flag overrides).

A section header is a comment styled as a banner: a title framed by a run of
fill characters. Real code accumulates many variants; `sectionise` detects them
and rewrites each to the same shape.

## What it standardises

| Variant | Example in |
| --- | --- |
| Single-line, framed both sides | `# ------- Loading models -------` |
| Single-line, filled one side | `# Ancillary functions ----------` |
| Unicode fill (em/en dash, box rules) | `# ——— Loading models ———` |
| Three-line box | a rule line, a title comment, a rule line |

All of them normalise to the configured style, for example the single-line form:

```
# ---------------------------- Loading models -----------------------------
```

Detection is conservative: only full-line comments whose content is framed by a
fill run of at least `min_run` characters are touched. Ordinary comments,
trailing comments, and commented-out code (`# print("=== run ===")`) are left
alone. Title-less rules (`# --------`) are ignored unless `dividers` is enabled.

A title too long to fit the chosen style is reported as an error suggesting a
shorter title or the multi-line `box` style, rather than being silently
overflowed.

## Installation

```bash
pip install sectionise
# or: uv add sectionise    (add to a project)
# or: uvx sectionise ...   (run without installing)
```

## Use as a pre-commit hook

```yaml
- repo: https://github.com/MitchellNeedham/sectionise
  rev: 0.1.0
  hooks:
      - id: sectionise
```

It fixes in place and fails the commit if it changed anything (re-stage and
commit again), the same way `ruff-format` behaves.

## Use as a CLI

```bash
sectionise path/to/file.py          # fix in place
sectionise --check path/to/file.py  # report only, no writes
```

## Configuration

Settings resolve with the precedence **flags > `[tool.sectionise]` > defaults**.
Put shared settings in each repo's `pyproject.toml`:

```toml
[tool.sectionise]
width = 88
style = "single"        # or "box"
fill = "-"
detect_chars = "-=*_~#—–─═"
min_run = 3
require_both_sides = false
dividers = false
# max_title = 60        # optional hard cap on title length
```

| Setting | Flag | Default | Meaning |
| --- | --- | --- | --- |
| `width` | `--width` | `88` | Target total line length. |
| `style` | `--style` | `single` | Output form: `single` line or 3-line `box`. |
| `fill` | `--fill` | `-` | Output fill character. |
| `detect_chars` | `--detect-chars` | `-=*_~#—–─═` | Characters recognised as fill in input. |
| `min_run` | `--min-run` | `3` | Minimum fill-run length to count as a banner side. |
| `require_both_sides` | `--require-both-sides` | `false` | Only treat both-sided comments as banners. |
| `dividers` | `--dividers` | `false` | Also standardise title-less rules. |
| `max_title` | `--max-title` | none | Hard cap on title length, on top of the width fit. |

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

## Publishing

Releases go to [PyPI](https://pypi.org/project/sectionise/) on a version tag.

- **Set the token once.** In the GitHub repo, add a repository secret named
  `PYPI_API_TOKEN` holding a PyPI API token
  (Settings > Secrets and variables > Actions).
- **Release.** Tag a version and push it; the publish workflow builds and
  uploads:

  ```bash
  git tag 0.1.0
  git push origin 0.1.0
  ```

  The version comes from the tag via `hatch-vcs`, so the tag is the single
  source of truth.

To publish from your machine instead, copy `.env.example` to `.env`, paste your
token, and run:

```bash
set -a && source .env && set +a
uv build && uv publish dist/*
```

## License

MIT. See [LICENSE](LICENSE).
