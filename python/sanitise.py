# /// script
# requires-python = ">=3.11"
# dependencies = ["lxml>=5", "pillow>=10"]
# ///
"""Hide school details in Word documents before Claude reads them. Undo with restore.py.

Run it yourself in Terminal, never through Claude:

    uv run sanitise.py ORIGINALS_FOLDER FOR_CLAUDE_FOLDER

For every .docx in ORIGINALS_FOLDER it writes a copy to FOR_CLAUDE_FOLDER in which
  * every term in ~/.school-sanitiser/config.toml, and every email address, web
    address and phone number, is replaced by a placeholder of the same length, in the
    body, headers, footers, footnotes, comments, tracked changes, text boxes, links,
    document properties and the file name;
  * comment and tracked-change author names are replaced the same way;
  * the pictures you choose are saved to ~/.school-sanitiser/vault/images and replaced
    by a white picture of the same size in the same place. All pictures are first shown
    on one page in your browser, numbered; you type the numbers to hide in Terminal
    (Enter hides them all). A picture used in several documents is asked about once.
    --hide-pictures "1 3" (or all, none) answers without asking.
Then it re-reads each copy and reports anything on the list that is still there.
Other file types are not copied. Nothing confidential is printed.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import sys
import webbrowser
import zipfile
from pathlib import Path

import school_vault as sv

NOTE = """\
About these documents

Words made of capital letters that start with QZ and are padded with Q (for example
QZAQQQQQQQ) stand in for confidential names, addresses and contact details. Keep each
one exactly as written, letter for letter, wherever it belongs in your work. They are
swapped back afterwards.

White pictures stand in for the original pictures. Leave them where they are; you may
copy them into new documents. They are swapped back afterwards too.
"""


BROWSER_SHOWS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".jfif": "jpeg", ".gif": "gif",
                 ".bmp": "bmp", ".webp": "webp", ".svg": "svg+xml"}


def is_thumbnail(name: str) -> bool:
    """docProps/thumbnail.* is a picture of page one: always hidden, never asked about."""
    return name.startswith("docProps/")


def collect_pictures(docs: list[Path]) -> list[dict]:
    """Every distinct picture across the documents, in order of first appearance."""
    pics: dict[str, dict] = {}
    for src in docs:
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                if Path(n).suffix.lower() in sv.IMAGE_EXT and not is_thumbnail(n):
                    data = z.read(n)
                    p = pics.setdefault(sv.sha(data), {"sha": sv.sha(data), "data": data,
                                                       "ext": Path(n).suffix.lower(), "docs": []})
                    if src.name not in p["docs"]:
                        p["docs"].append(src.name)
    return list(pics.values())


def contact_sheet(pictures: list[dict]) -> str:
    cards = []
    for i, p in enumerate(pictures, 1):
        kind = BROWSER_SHOWS.get(p["ext"])
        shown = (f'<img src="data:image/{kind};base64,{base64.b64encode(p["data"]).decode()}">' if kind
                 else '<div class="none">No preview: Word drawing format</div>')
        cards.append(f'<figure><div class="n">{i}</div>{shown}<figcaption>'
                     f'{html.escape(", ".join(p["docs"]))}</figcaption></figure>')
    return ("<!doctype html><meta charset=utf-8><title>Choose pictures to hide</title><style>"
            "body{font:15px system-ui;margin:16px;background:#fff;color:#111}"
            "main{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:16px}"
            "figure{margin:0;border:1px solid #ccc;border-radius:8px;padding:8px;position:relative}"
            "img{max-width:100%;max-height:200px;display:block;margin:auto;background:"
            "repeating-conic-gradient(#eee 0 25%,#fff 0 50%) 0 0/16px 16px}"
            ".n{position:absolute;top:6px;left:6px;background:#111;color:#fff;border-radius:12px;"
            "padding:2px 9px;font-weight:700}.none{height:120px;display:grid;place-items:center;color:#666}"
            "figcaption{font-size:12px;color:#555;margin-top:6px}</style>"
            "<h1>Which pictures should Claude not see?</h1><p>Type their numbers in Terminal. "
            "This page is deleted when you answer.</p><main>" + "".join(cards) + "</main>")


def parse_choice(answer: str, count: int) -> set[int] | None:
    answer = answer.strip().lower()
    if answer in ("", "all"):
        return set(range(1, count + 1))
    if answer == "none":
        return set()
    chosen = set()
    for part in answer.replace(",", " ").split():
        lo, _, hi = part.partition("-")
        if not (lo.isdigit() and (not hi or hi.isdigit())):
            return None
        rng = range(int(lo), int(hi or lo) + 1)
        if not rng or rng[0] < 1 or rng[-1] > count:
            return None
        chosen.update(rng)
    return chosen


def choose_pictures(pictures: list[dict], answer: str | None) -> set[str]:
    """Show the pictures in the browser and ask which to hide. Returns their hashes."""
    if not pictures:
        return set()
    if answer is None and not sys.stdin.isatty():
        answer = "all"  # nobody to ask: hide everything
    if answer is not None:
        chosen = parse_choice(answer, len(pictures))
        if chosen is None:
            sys.exit(f"--hide-pictures {answer!r} not understood")
    else:
        sheet = sv.HOME / "pictures-to-choose.html"
        sheet.write_text(contact_sheet(pictures), encoding="utf-8")
        webbrowser.open(sheet.as_uri())
        print(f"{len(pictures)} different pictures are shown in your browser.")
        try:
            while (chosen := parse_choice(input(
                    "Numbers of the pictures to hide (e.g. 1 3 5-7), 'all' or 'none' [all]: "),
                    len(pictures))) is None:
                print("Not understood; try again.")
        finally:
            sheet.unlink(missing_ok=True)
    return {pictures[i - 1]["sha"] for i in chosen}


def check(path: Path, cfg: dict, vault: sv.Vault, hide: set[str]) -> list[str]:
    probe = sv.Finder(cfg, None)
    problems = []

    def image_fn(data, name):
        if vault.blank_id(data) is None and (sv.sha(data) in hide or is_thumbnail(name)):
            problems.append(f"picture not replaced: {name}")
        return data
    sv.docx_transform(path, io.BytesIO(), probe.text_fn, image_fn)
    return problems + [f"{rule.replace('_', ' ')} still in the text" for rule in dict.fromkeys(probe.found)]


def sanitise_folder(src_dir: Path, out_dir: Path, hide: set[str], cfg: dict) -> str:
    """Clean every .docx in src_dir into out_dir, hiding the pictures whose hashes are in hide."""
    vault = sv.Vault()
    lines, skipped, totals = [], 0, {"CLEAN": 0, "CHECK BY HAND": 0}
    for src in sorted(p for p in src_dir.rglob("*") if p.is_file() and not p.name.startswith((".", "~$"))):
        rel = src.relative_to(src_dir)
        if src.suffix.lower() != ".docx":
            skipped += 1
            continue
        finder = sv.Finder(cfg, vault)
        finder.counts["kept"] = 0
        clean_rel = Path(*[finder.tokenise(part, phones=False) for part in rel.parts])
        dst = out_dir / clean_rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        def image_fn(data, name, rel=rel, finder=finder):
            if sv.sha(data) not in hide and not is_thumbnail(name):
                finder.counts["kept"] += 1
                return data
            finder.counts["pictures"] += 1
            return vault.blank_for(data, name, f"{rel} / {name}")
        try:
            notes = sv.docx_transform(src, dst, finder.text_fn, image_fn)
            vault.save()
            notes += check(dst, cfg, vault, hide)
        except Exception as e:  # a half-cleaned file must never be left for Claude
            dst.unlink(missing_ok=True)
            vault.save()
            lines += [f"NOT COPIED     {clean_rel}", f"               ! could not be processed ({type(e).__name__})"]
            continue
        status = "CHECK BY HAND" if notes else "CLEAN"
        totals[status] += 1
        lines.append(f"{status:13}  {clean_rel}")
        lines.append(f"               replaced: {finder.counts['text']} pieces of text, "
                     f"{finder.counts['pictures']} pictures; kept {finder.counts['kept']} pictures as you chose")
        lines += [f"               ! {n}" for n in notes]

    (out_dir / "ABOUT-THESE-DOCUMENTS.txt").write_text(NOTE, encoding="utf-8")
    return "\n".join([
        "School file sanitiser",
        f"Word documents: {totals['CLEAN']} clean, {totals['CHECK BY HAND']} to check by hand. "
        f"Other files not copied: {skipped}.", "", *lines, "",
        "CLEAN means nothing from the config was found afterwards. Names not in the config",
        "(pupils, other staff) are not caught: open each document and read it before Claude does.",
        f"Originals of everything replaced are in {sv.VAULT}; restore.py puts them back."])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("originals", type=Path, help="folder holding the documents as they arrived")
    ap.add_argument("for_claude", type=Path, help="new, empty folder for the copies Claude may read")
    ap.add_argument("--hide-pictures", metavar="NUMBERS",
                    help='answer the picture question without asking: e.g. "1 3 5-7", all, none')
    args = ap.parse_args()
    cfg = sv.load_config()

    src_dir, out_dir = args.originals.resolve(), args.for_claude.resolve()
    if not src_dir.is_dir():
        sys.exit(f"{src_dir} is not a folder")
    if out_dir == src_dir or src_dir in out_dir.parents or sv.HOME in out_dir.parents:
        sys.exit("Put the folder for Claude outside the originals folder and outside the vault.")
    if out_dir.exists() and any(out_dir.iterdir()):
        sys.exit(f"{out_dir} is not empty; give a new folder.")
    out_dir.mkdir(parents=True, exist_ok=True)

    docs = sorted(p for p in src_dir.rglob("*.docx") if p.is_file() and not p.name.startswith((".", "~$")))
    hide = choose_pictures(collect_pictures(docs), args.hide_pictures)

    print(sanitise_folder(src_dir, out_dir, hide, cfg))


if __name__ == "__main__":
    main()
