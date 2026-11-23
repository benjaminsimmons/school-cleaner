"""Sanitise a Word document for an invented school, edit it as Claude would, restore it.

Run from the project folder:
    uv run --with pytest --with python-docx --with lxml --with pillow pytest tests
"""
import hashlib
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import docx
import pytest
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
CONFIG = 'terms = ["Hollybrook Primary School", "Hollybrook Primary", "Hollybrook", "Mrs Patel"]\n'
SECRETS = [b"Hollybrook", b"HOLLYBROOK", b"Patel", b"Alex", b"9876", b"hollybrook.vic"]


def run(script, *args, home):
    env = {**os.environ, "SCHOOL_SANITISER_HOME": str(home)}
    return subprocess.run([sys.executable, PROJECT / script, *map(str, args)],
                          env=env, capture_output=True, text=True, check=True).stdout


def media(path):
    with zipfile.ZipFile(path) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith("word/media/")}


def make_original(folder: Path) -> Path:
    folder.mkdir()
    d = docx.Document()
    for colour, size in [("red", (300, 200)), ("blue", (64, 64)), ("red", (300, 200))]:
        buf = io.BytesIO()
        Image.new("RGB", size, colour).save(buf, "PNG")
        buf.seek(0)
        d.add_picture(buf)  # the red one twice: a logo repeated
    d.save(folder / "pictures.docx")
    return folder / "pictures.docx"


def test_only_the_chosen_pictures_are_hidden_and_they_come_back(tmp_path):
    home = tmp_path / "private"
    home.mkdir()
    (home / "config.toml").write_text(CONFIG)
    original = make_original(tmp_path / "orig")
    # Two distinct pictures, numbered in order of appearance: 1 red, 2 blue. Hide red only.
    report = run("sanitise.py", tmp_path / "orig", tmp_path / "clean", "--hide-pictures", "1", home=home)
    assert "kept 1 pictures as you chose" in report and "CLEAN" in report
    clean = tmp_path / "clean" / "pictures.docx"
    with zipfile.ZipFile(clean) as z:
        colours = sorted(Image.open(io.BytesIO(z.read(n))).convert("RGB").getpixel((5, 5))
                         for n in z.namelist() if n.startswith("word/media/"))
    assert colours == [(0, 0, 255), (255, 255, 255)]
    run("restore.py", tmp_path / "clean", tmp_path / "final", home=home)
    assert media(tmp_path / "final" / "pictures.docx") == media(original)


def test_picture_answers_are_read_as_numbers_ranges_all_or_none():
    sys.path.insert(0, str(PROJECT))
    from sanitise import parse_choice
    assert parse_choice("1 3, 5-6", 6) == {1, 3, 5, 6}
    assert parse_choice("", 3) == parse_choice("all", 3) == {1, 2, 3}
    assert parse_choice("none", 3) == set()
    assert parse_choice("7", 3) is None and parse_choice("one", 3) is None


@pytest.fixture
def trip(tmp_path):
    home = tmp_path / "private"
    home.mkdir()
    (home / "config.toml").write_text(CONFIG)
    orig = tmp_path / "orig"
    orig.mkdir()
    d = docx.Document()
    d.core_properties.author = "Alex Smith"
    d.sections[0].header.paragraphs[0].text = "HOLLYBROOK Primary School"
    p = d.add_paragraph()
    for piece in ["Prep Forces unit, Holly", "brook Primary. Ask Mrs ", "Patel on 03 9876 5432."]:
        p.add_run(piece)  # names split across runs, as Word does
    d.add_paragraph("Email office@hollybrook.vic.edu.au. Measure 3 1/4 metres, 12 balls.")
    for colour, size in [("red", (300, 200)), ("blue", (64, 64))]:
        buf = io.BytesIO()
        Image.new("RGB", size, colour).save(buf, "PNG")
        buf.seek(0)
        d.add_picture(buf)
    d.add_comment(d.paragraphs[0].runs, text="Check with Mrs Patel", author="Alex Smith", initials="GS")
    original = orig / "Hollybrook unit.docx"
    d.save(original)
    run("sanitise.py", orig, tmp_path / "clean", home=home)
    return tmp_path, home, original


def test_clean_copy_holds_no_school_details_and_pictures_are_same_size_blanks(trip):
    tmp, home, original = trip
    clean = next((tmp / "clean").glob("*.docx"))
    assert "Hollybrook" not in clean.name
    with zipfile.ZipFile(clean) as z:
        everything = b"".join(z.read(n) for n in z.namelist())
        sizes = [Image.open(io.BytesIO(z.read(n))).size for n in sorted(z.namelist()) if n.startswith("word/media/")]
        colours = {Image.open(io.BytesIO(z.read(n))).convert("RGB").getpixel((5, 5))
                   for n in z.namelist() if n.startswith("word/media/")}
    assert [s for s in SECRETS if s in everything] == []
    assert sizes == [(300, 200), (64, 64)]
    assert colours == {(255, 255, 255)}


def test_placeholders_keep_the_length_of_the_text_they_replace(trip):
    tmp, home, original = trip
    before = docx.Document(original).paragraphs[0].text
    after = docx.Document(next((tmp / "clean").glob("*.docx"))).paragraphs[0].text
    assert len(after) == len(before) and after != before
    assert after.startswith("Prep Forces unit, QZ")


def test_restore_puts_back_text_and_pictures_in_edited_and_new_documents(trip):
    tmp, home, original = trip
    clean = next((tmp / "clean").glob("*.docx"))
    work = tmp / "claude"
    work.mkdir()
    # Act as Claude: edit the document, and write a new one reusing a placeholder.
    d = docx.Document(clean)
    token = d.paragraphs[0].text.split()[3].rstrip(".")
    d.add_paragraph(f"Year 1 builds on the {token} Prep unit.")
    d.save(work / clean.name)
    new = docx.Document()
    new.add_paragraph(f"Year 1 Forces for {token}")
    rid = d.inline_shapes[0]._inline.graphic.graphicData.pic.blipFill.blip.embed
    new.add_picture(io.BytesIO(d.part.related_parts[rid].blob))
    new.save(work / f"{token} Year 1.docx")

    report = run("restore.py", work, tmp / "final", home=home)
    assert "to check by hand: 0" in report

    edited = docx.Document(tmp / "final" / "Hollybrook unit.docx")
    first = docx.Document(original).paragraphs[0]
    assert [r.text for r in edited.paragraphs[0].runs] == [r.text for r in first.runs]
    assert edited.sections[0].header.paragraphs[0].text == "HOLLYBROOK Primary School"
    assert edited.paragraphs[-1].text == "Year 1 builds on the Hollybrook Primary Prep unit."
    assert edited.core_properties.author == "Alex Smith"
    assert media(tmp / "final" / "Hollybrook unit.docx") == media(original)
    with zipfile.ZipFile(tmp / "final" / "Hollybrook unit.docx") as z:
        comments = z.read("word/comments.xml")
    assert b'w:author="Alex Smith"' in comments and b"Mrs Patel" in comments

    made = tmp / "final" / "Hollybrook Primary Year 1.docx"
    assert docx.Document(made).paragraphs[0].text == "Year 1 Forces for Hollybrook Primary"
    assert list(media(made).values()) == [media(original)["word/media/image1.png"]]
