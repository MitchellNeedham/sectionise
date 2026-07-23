"""End-to-end CLI behaviour: settings resolution, file I/O, and exit codes.

These use real files under `tmp_path` (no `pyproject.toml` in its ancestry, so
built-in defaults show through unless a test writes its own config).
"""

import io

import pytest

from sectionise.cli import main


def write(path, text, encoding="utf-8"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


# ---------------------------- Per-language defaults --------------------------
@pytest.mark.parametrize(
    "suffix, comment, expected_width",
    [
        (".py", "#", 88),
        (".js", "//", 80),
        (".rs", "//", 100),
        (".cs", "//", 120),
    ],
)
def test_builtin_default_width_per_language(tmp_path, suffix, comment, expected_width):
    f = write(tmp_path / f"f{suffix}", f"{comment} --- x ---\n")
    assert main([str(f)]) == 1
    assert len(f.read_text(encoding="utf-8").splitlines()[0]) == expected_width


# ------------------------------ Config precedence ----------------------------
def test_global_and_per_language_override(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        "[tool.sectionise]\nwidth = 70\n\n[tool.sectionise.language.javascript]\nwidth = 60\n",
    )
    py = write(tmp_path / "a.py", "# --- x ---\n")
    js = write(tmp_path / "a.js", "// --- x ---\n")
    rs = write(tmp_path / "a.rs", "// --- x ---\n")
    main([str(tmp_path)])
    assert len(py.read_text().splitlines()[0]) == 70  # global beats builtin 88
    assert len(js.read_text().splitlines()[0]) == 60  # language beats global
    assert len(rs.read_text().splitlines()[0]) == 70  # global beats builtin 100


def test_flag_overrides_everything(tmp_path):
    write(tmp_path / "pyproject.toml", "[tool.sectionise]\nwidth = 70\n")
    f = write(tmp_path / "a.py", "# --- x ---\n")
    main(["--width", "50", str(f)])
    assert len(f.read_text().splitlines()[0]) == 50


# --------------------------------- Exit codes --------------------------------
def test_exit_zero_when_clean(tmp_path, capsys):
    f = write(tmp_path / "a.py", "# --- x ---\ncode = 1\n")
    main([str(f)])  # normalise first
    assert main(["--check", str(f)]) == 0


def test_exit_one_when_would_reformat(tmp_path):
    f = write(tmp_path / "a.py", "# --- x ---\n")
    assert main(["--check", str(f)]) == 1


def test_exit_two_on_over_long_title(tmp_path, capsys):
    f = write(tmp_path / "a.py", "# --- a very long section title indeed ---\n")
    assert main(["--check", "--width", "20", str(f)]) == 2
    err = capsys.readouterr().err
    assert "section title" in err  # errors go to stderr


# ------------------------------- Check and diff ------------------------------
def test_check_does_not_write(tmp_path):
    f = write(tmp_path / "a.py", "# --- x ---\n")
    main(["--check", str(f)])
    assert f.read_text() == "# --- x ---\n"


def test_diff_prints_and_does_not_write(tmp_path, capsys):
    f = write(tmp_path / "a.py", "# --- x ---\n")
    rc = main(["--diff", "--width", "40", str(f)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "-# --- x ---" in out and "+# " in out
    assert f.read_text() == "# --- x ---\n"  # unchanged on disk


# ---------------------------------- Stdin ------------------------------------
def test_stdin_writes_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("# --- hi ---\n"))
    rc = main(["--width", "40", "-"])
    out = capsys.readouterr().out
    assert rc == 1
    assert len(out.splitlines()[0]) == 40


def test_stdin_filename_picks_syntax(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("// --- hi ---\n"))
    main(["--width", "40", "--stdin-filename", "x.js", "-"])
    assert capsys.readouterr().out.startswith("// ")


# ------------------------------ Directory walk -------------------------------
def test_directory_walk_skips_vendored_dirs(tmp_path):
    write(tmp_path / "pkg" / "y.py", "# --- y ---\n")
    write(tmp_path / ".venv" / "x.py", "# --- x ---\n")
    main([str(tmp_path)])
    assert (tmp_path / ".venv" / "x.py").read_text() == "# --- x ---\n"  # untouched
    assert (tmp_path / "pkg" / "y.py").read_text() != "# --- y ---\n"  # reformatted


def test_missing_path_warns_on_stderr(tmp_path, capsys):
    main([str(tmp_path / "nope.py")])
    assert "not found" in capsys.readouterr().err


def test_unsupported_explicit_file_warns(tmp_path, capsys):
    write(tmp_path / "notes.txt", "hello\n")
    main([str(tmp_path / "notes.txt")])
    assert "unsupported" in capsys.readouterr().err


# ---------------------------- Custom and shebang -----------------------------
def test_custom_language_from_config(tmp_path):
    write(
        tmp_path / "pyproject.toml",
        '[tool.sectionise.language.vue]\nsuffixes = [".vue"]\ncomments = ["//"]\nwidth = 50\n',
    )
    f = write(tmp_path / "c.vue", "// --- header ---\n")
    main([str(tmp_path)])
    assert len(f.read_text().splitlines()[0]) == 50


def test_malformed_custom_language_errors(tmp_path, capsys):
    write(tmp_path / "pyproject.toml", '[tool.sectionise.language.bad]\nsuffixes = [".bad"]\n')
    f = write(tmp_path / "f.bad", "// --- x ---\n")
    assert main([str(f)]) == 2
    assert "custom language 'bad'" in capsys.readouterr().err


def test_shebang_extensionless_script(tmp_path):
    f = write(tmp_path / "myscript", "#!/usr/bin/env python3\n# --- setup ---\nx = 1\n")
    main([str(f)])
    assert len(f.read_text().splitlines()[1]) == 88  # python default width


# --------------------------- Encoding and endings ----------------------------
def test_lf_stays_lf(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b"# --- x ---\ncode = 1\n")
    main([str(f)])
    data = f.read_bytes()
    assert b"\r\n" not in data and b"\n" in data


def test_crlf_stays_crlf(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b"# --- x ---\r\ncode = 1\r\n")
    main([str(f)])
    data = f.read_bytes()
    assert data.count(b"\r\n") == 2 and data.count(b"\n") == 2


def test_bom_preserved(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b"\xef\xbb\xbf# --- x ---\ncode = 1\n")
    main([str(f)])
    data = f.read_bytes()
    assert data[:3] == b"\xef\xbb\xbf" and data.count(b"\xef\xbb\xbf") == 1


def test_non_utf8_skipped_by_default(tmp_path, capsys):
    f = tmp_path / "a.py"
    f.write_bytes(b"# --- caf\xe9 ---\ncode = 1\n")  # latin-1
    assert main([str(f)]) == 0
    assert "not valid utf-8" in capsys.readouterr().err
    assert f.read_bytes() == b"# --- caf\xe9 ---\ncode = 1\n"  # untouched


def test_explicit_encoding_processes_latin1(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b"# --- caf\xe9 ---\ncode = 1\n")
    assert main(["--encoding", "latin-1", str(f)]) == 1
    data = f.read_bytes()
    assert b"\xe9" in data and len(data.split(b"\n")[0]) == 88


def test_unknown_encoding_errors(tmp_path, capsys):
    f = write(tmp_path / "a.py", "# --- x ---\n")
    assert main(["--encoding", "not-a-codec", str(f)]) == 2
    assert "unknown encoding" in capsys.readouterr().err
