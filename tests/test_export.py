"""The one part of export.py that reads something other than the database."""

from pathlib import Path

from dynovia.export import app_version


def test_reads_the_constant_app_js_declares(tmp_path: Path) -> None:
    js = tmp_path / "app.js"
    js.write_text('"use strict";\n\nconst APP_VERSION = 7;\n\nconst DATA = [];\n')
    assert app_version(js) == 7


def test_ignores_the_word_elsewhere_in_the_file(tmp_path: Path) -> None:
    # Only the declaration counts. A mention in a comment or a string is not
    # the version, and matching one would pin meta.json to whatever prose
    # happened to be written near it.
    js = tmp_path / "app.js"
    js.write_text(
        "/* APP_VERSION = 99 opisane w komentarzu */\n"
        'const NOTE = "const APP_VERSION = 42;";\n'
        "const APP_VERSION = 3;\n"
    )
    assert app_version(js) == 3


def test_missing_constant_gives_no_version(tmp_path: Path) -> None:
    # No version in meta.json means the page says nothing. A banner pointing
    # at a version that cannot be named would be worse than no banner.
    js = tmp_path / "app.js"
    js.write_text("const DATA = [];\n")
    assert app_version(js) is None


def test_missing_file_gives_no_version(tmp_path: Path) -> None:
    assert app_version(tmp_path / "nie-ma-takiego.js") is None
