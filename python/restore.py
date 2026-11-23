# /// script
# requires-python = ">=3.11"
# dependencies = ["lxml>=5", "pillow>=10"]
# ///
"""Put the school details back into documents Claude has worked on.

Run it yourself in Terminal, never through Claude:

    uv run restore.py FROM_CLAUDE_FOLDER FINISHED_FOLDER

Every file in FROM_CLAUDE_FOLDER is copied to FINISHED_FOLDER with
  * every placeholder (QZA..., QZB...) turned back into the original text, in the
    document and its file name;
  * every white placeholder picture swapped back for the original picture, including
    pictures Claude copied into a new document.
.docx files are restored in full; plain text files (.txt .md .csv .html ...) have their
text restored; anything else is copied unchanged. The report counts what was put back
and lists any placeholder that could not be matched. Nothing confidential is printed.
Keep FINISHED_FOLDER away from Claude: it holds the real details again.
"""
from __future__ import annotations

import argparse
import io
import shutil
import sys
from pathlib import Path

import school_vault as sv


def leftovers_docx(path: Path, vault: sv.Vault) -> list[str]:
    problems = []

    def text_fn(text, kind):
        problems.extend("placeholder text still there" for _ in sv.TOKEN_RE.finditer(text))
        return text

    def image_fn(data, name):
        if vault.blank_id(data):
            problems.append(f"placeholder picture still there: {name}")
        return data
    sv.docx_transform(path, io.BytesIO(), text_fn, image_fn)
    return list(dict.fromkeys(problems))


def restore_folder(src_dir: Path, out_dir: Path) -> str:
    """Copy src_dir to out_dir with every placeholder and placeholder picture put back."""
    vault = sv.Vault()

    lines, bad = [], 0
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file() and not p.name.startswith((".", "~$"))):
        rel = src.relative_to(src_dir)
        if rel.name == "ABOUT-THESE-DOCUMENTS.txt":
            continue
        counts = {"text": 0, "pictures": 0}
        final_rel = Path(*[vault.untokenise(part, counts) for part in rel.parts])
        dst = out_dir / final_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        notes = []
        if src.suffix.lower() == ".docx":
            def image_fn(data, name, counts=counts):
                img_id = vault.blank_id(data)
                if img_id is None or img_id not in vault.data["images"]:
                    return data
                counts["pictures"] += 1
                return vault.original_image(img_id)
            try:
                sv.docx_transform(src, dst, lambda t, kind: vault.untokenise(t, counts), image_fn)
                notes = leftovers_docx(dst, vault)
            except Exception as e:
                shutil.copy2(src, dst)
                notes = [f"could not be opened as a Word document ({type(e).__name__}); copied unchanged"]
        else:
            try:
                text = src.read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                shutil.copy2(src, dst)
                if vault.blank_id(src.read_bytes()):
                    shutil.copyfile(vault.original_image(vault.blank_id(src.read_bytes())), dst)
                    counts["pictures"] += 1
            else:
                restored = vault.untokenise(text, counts)
                dst.write_text(restored, encoding="utf-8")
                if sv.TOKEN_RE.search(restored):
                    notes = ["placeholder text still there"]
        bad += bool(notes)
        lines.append(f"{'CHECK BY HAND' if notes else 'RESTORED':13}  {rel}")
        lines.append(f"               put back: {counts['text']} pieces of text, {counts['pictures']} pictures")
        lines += [f"               ! {n}" for n in notes]

    files = sum(1 for line in lines if not line.startswith(" "))
    return "\n".join(["School file restore", f"Files: {files}, to check by hand: {bad}", "", *lines, "",
                     "CHECK BY HAND usually means Claude changed a placeholder (e.g. QZAQQ into QZAQQs).",
                     "Search the document for QZ and fix those few by hand."])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("from_claude", type=Path, help="folder holding the documents Claude produced")
    ap.add_argument("finished", type=Path, help="new, empty folder for the restored documents")
    args = ap.parse_args()

    src_dir, out_dir = args.from_claude.resolve(), args.finished.resolve()
    if not src_dir.is_dir():
        sys.exit(f"{src_dir} is not a folder")
    if out_dir == src_dir or src_dir in out_dir.parents:
        sys.exit("Put the finished folder outside the folder Claude worked in.")
    if out_dir.exists() and any(out_dir.iterdir()):
        sys.exit(f"{out_dir} is not empty; give a new folder.")
    if not sv.MANIFEST.exists():
        sys.exit(f"No vault at {sv.VAULT}: nothing to restore from.")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(restore_folder(src_dir, out_dir))


if __name__ == "__main__":
    main()
