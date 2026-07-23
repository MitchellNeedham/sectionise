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
| Block comment | `/* ===== Loading models ===== */` |
| Three-line box | a rule line, a title comment, a rule line |

All of them normalise to the configured style, for example the single-line form:

```
# ---------------------------- Loading models -----------------------------
```

Detection is conservative: only full-line comments whose content is framed by a
fill run of at least `min_run` characters are touched. Ordinary comments,
trailing comments, and commented-out code (`# print("=== run ===")`) are left
alone, as are banner-looking lines that actually sit inside a string literal (a
docstring, here-doc, or template literal). Title-less rules (`# --------`) are
ignored unless `dividers` is enabled.

A title too long to fit the chosen style is reported as an error suggesting a
shorter title or the multi-line `box` style, rather than being silently
overflowed.

## Supported languages

Extensions are recognised out of the box, each with an opinionated default width
matching its dominant formatter or style guide (Black 88 for Python, Prettier 80
for the web languages, rustfmt/Google 100, Microsoft/SwiftLint/RuboCop 120). Any
default can be overridden globally or per language (see
[Configuration](#configuration)).

Each language rewrites at its own default width, in its own comment syntax:

```
# python (88)
# ----------------------------------- Loading models -----------------------------------

// javascript (80)
// ------------------------------ Loading models -------------------------------

// csharp (120)
// -------------------------------------------------- Loading models ---------------------------------------------------

-- sql (88)
-- ---------------------------------- Loading models -----------------------------------

/* css (80) */
/* ----------------------------- Loading models ----------------------------- */

<!-- html (80) -->
<!-- --------------------------- Loading models ---------------------------- -->
```

The `Language` column below is the canonical name used to key a
`[tool.sectionise.language.<name>]` override.

| Language | Extensions | Default width |
| --- | --- | --- |
| `python` | `.py`, `.pyi` | 88 |
| `shell` | `.sh`, `.bash` | 80 |
| `toml` | `.toml` | 80 |
| `yaml` | `.yaml`, `.yml` | 80 |
| `ini` | `.ini`, `.cfg` | 80 |
| `ruby` | `.rb` | 120 |
| `perl` | `.pl`, `.pm` | 80 |
| `r` | `.r` | 80 |
| `javascript` | `.js`, `.jsx`, `.mjs`, `.cjs` | 80 |
| `typescript` | `.ts`, `.tsx` | 80 |
| `c` | `.c`, `.h` | 80 |
| `cpp` | `.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh` | 80 |
| `csharp` | `.cs` | 120 |
| `java` | `.java` | 100 |
| `kotlin` | `.kt`, `.kts` | 100 |
| `swift` | `.swift` | 120 |
| `scala` | `.scala` | 80 |
| `dart` | `.dart` | 80 |
| `go` | `.go` | 100 |
| `rust` | `.rs` | 100 |
| `css` | `.css` | 80 |
| `scss` | `.scss`, `.less` | 80 |
| `sql` | `.sql` | 88 |
| `lua` | `.lua` | 80 |
| `terraform` | `.tf` | 80 |
| `html` | `.html`, `.htm` | 80 |
| `xml` | `.xml` | 80 |
| `markdown` | `.md` | 80 |

Extensionless scripts are recognised by their `#!` shebang (for example
`#!/usr/bin/env python`). Anything not listed can be added as a
[custom language](#custom-languages).

## Installation

```bash
pip install sectionise
# or: uv add sectionise    (add to a project)
# or: uvx sectionise ...   (run without installing)
```

## Use as a pre-commit hook

```yaml
- repo: https://github.com/MitchellNeedham/sectionise
  rev: 0.3.0
  hooks:
      - id: sectionise
```

It fixes in place and fails the commit if it changed anything (re-stage and
commit again), the same way `ruff-format` behaves.

## Use as a CLI

```bash
sectionise path/to/file.py          # fix in place
sectionise src/                     # walk a directory (skips vendored trees)
sectionise --check path/to/file.py  # report only, no writes
sectionise --diff path/to/file.py   # print a unified diff, no writes
cat file.py | sectionise --stdin-filename file.py -   # read stdin, write stdout
```

Exit codes: `0` nothing to change, `1` something was (or would be) reformatted,
`2` a hard error such as an over-long title (reported on stderr).

## Configuration

Settings resolve by precedence, highest first:

1. command-line flag (applies to every file)
2. `[tool.sectionise.language.<name>]` in the nearest `pyproject.toml` (that language)
3. `[tool.sectionise]` in the nearest `pyproject.toml` (every file)
4. the built-in per-language default
5. the built-in global default

In a monorepo each file uses its own nearest `pyproject.toml`, so per-package
overrides are honoured.

```toml
[tool.sectionise]
style = "single"        # or "box"
fill = "-"
align = "centre"        # or "left"
fill_mode = "width"     # or "fixed"
fill_count = 3          # fill characters per run when fill_mode = "fixed"
bookend = false         # close a single-line banner with a mirror of the opener
detect_chars = "-=*_~#—–─═"
min_run = 3
require_both_sides = false
dividers = false
boxes = true
tab_width = 8
encoding = "utf-8"
# width = 88            # omit to keep the per-language defaults above
# max_title = 60        # optional hard cap on title length
```

| Setting | Flag | Default | Meaning |
| --- | --- | --- | --- |
| `width` | `--width` | per language | Target total line length. |
| `style` | `--style` | `single` | Output form: `single` line or 3-line `box`. |
| `fill` | `--fill` | `-` | Output fill. One or more characters; a multi-character fill is tiled to the width. |
| `align` | `--align` | `centre` | Single-line title placement: `centre` or `left`. |
| `fill_mode` | `--fill-mode` | `width` | Single-line fill: `width` (pad to `width`) or `fixed` (a set count per run). |
| `fill_count` | `--fill-count` | `3` | Fill characters per run when `fill_mode` is `fixed`. Must be at least `min_run`. |
| `bookend` | `--bookend` | `false` | Close a single-line banner with a mirror of the comment opener. Line comments only. |
| `detect_chars` | `--detect-chars` | `-=*_~#—–─═` | Characters recognised as fill in input. |
| `min_run` | `--min-run` | `3` | Minimum fill-run length to count as a banner side. |
| `require_both_sides` | `--require-both-sides` | `false` | Only treat both-sided comments as banners. |
| `dividers` | `--dividers` | `false` | Also standardise title-less rules. |
| `boxes` | `--boxes` | `true` | Recognise the three-line box form. |
| `tab_width` | `--tab-width` | `8` | Columns a leading tab occupies. |
| `encoding` | `--encoding` | `utf-8` | Text encoding used to read and write files. |
| `max_title` | `--max-title` | none | Hard cap on title length, on top of the width fit. |

### Example configurations

Box (three-line) headers, filled with `=`:

```toml
[tool.sectionise]
style = "box"
fill = "="
```

A Python banner then rewrites to a rule, the title, and a rule, all at the
88-column default:

```
# ======================================================================================
# Loading models
# ======================================================================================
```

Left-aligned single-line headers (title first, fill trailing):

```toml
[tool.sectionise]
align = "left"
```

```
# Loading models -----------------------------------------------------------------------
```

A fixed number of fill characters per run, so the line length follows the title
instead of the target width:

```toml
[tool.sectionise]
fill_mode = "fixed"
fill_count = 5
```

```
# ----- Loading models -----
# ----- Setup -----
```

`align` still decides the sides: `centre` (the default) puts a matching run on
both sides as above, while `left` keeps the trailing run only (`# Loading models
-----`). Box style and stand-alone dividers ignore `fill_mode` and always span
`width`.

Book-ended headers close with a mirror of the comment opener, so the line ends
the way it starts:

```toml
[tool.sectionise]
fill_mode = "fixed"
fill_count = 4
bookend = true
```

```
# ---- Loading models ---- #
```

The closing marker is always the opener, so it matches by construction: a `//`
comment closes with `//`, a `#` comment with `#`. Block comments already close
with their own token, so `bookend` leaves them alone. A trailing opener is
recognised on input whether or not `bookend` is set, so existing
`// --- x --- //` banners are picked up either way.

The fill can be more than one character; a multi-character fill is tiled and
truncated to reach the width:

```toml
[tool.sectionise]
fill = "=-"
fill_mode = "fixed"
fill_count = 8
```

```
# =-=-=-=- Loading models =-=-=-=-
```

A fill longer than the run it fills is truncated to its leading characters, so
in `fixed` mode a fill longer than `fill_count` is cut mid-motif.

Any non-space fill is valid, including an emoji:

```toml
[tool.sectionise]
fill = "🚀"
width = 40
```

```
# 🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀 Deploy to prod 🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀🚀
```

`width` counts characters, not display columns, so a double-width emoji fill
lands on target by count but is wider on screen.

### Per-language overrides

Tune any setting for one language with a `[tool.sectionise.language.<name>]`
table, where `<name>` is a `Language` from
[Supported languages](#supported-languages).

```toml
[tool.sectionise]
width = 100             # applies to every language

[tool.sectionise.language.python]
width = 79              # but Python uses 79

[tool.sectionise.language.markdown]
style = "box"
```

### Custom languages

Add a language the same way, giving it `suffixes` and `comments`. A `comments`
entry is a string (a line-comment opener) or a `[opener, closer]` pair. Other
keys set that language's defaults.

```toml
[tool.sectionise.language.vue]
suffixes = [".vue"]
comments = ["//", ["<!--", "-->"]]
width = 100
```

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
