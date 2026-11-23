"""Shared code for sanitise.py and restore.py (Word .docx files).

The confidential values live only in the private folder ~/.school-sanitiser/:
config.toml (what to hide) and vault/ (what was taken out). Nothing here prints them.

How a value is hidden:
  * Text: each confidential string becomes a placeholder of exactly the same length,
    e.g. "Hollybrook" -> "QZAQQQQQQQ": "QZ", a letter code (never Q), Q padding.
    The same string always gets the same placeholder, in every file.
  * Pictures: the original is saved in vault/images and replaced by a white picture of
    the same pixel size, in the same place. Its first two pixels carry a code (a marker
    colour, then the picture's number), so restore.py recognises it even after it has
    been copied into a new document.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tomllib
import zipfile
from pathlib import Path

from lxml import etree
from PIL import Image

HOME = Path(os.environ.get("SCHOOL_SANITISER_HOME", Path.home() / ".school-sanitiser"))
CONFIG = HOME / "config.toml"
VAULT = HOME / "vault"
MANIFEST = VAULT / "manifest.json"
IMAGES = VAULT / "images"

CONFIG_TEMPLATE = """\
# School file sanitiser settings. Claude must never read this file.
# Each entry is swapped for a same-length placeholder before Claude sees a document,
# and swapped back by restore.py. Matching ignores capitals; whole words only.
# At least 3 characters each.
terms = [
    "Hollybrook Primary School",
    "Hollybrook",
]

# Also swap these out wherever they appear (true or false).
email_addresses = true
web_addresses = true
phone_numbers = true
"""

PATTERNS = {
    "email_addresses": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "web_addresses": r"(?:https?://|www\.)[^\s<>\"']+"
                     r"|\b[\w-]+(?:\.[\w-]+)*\.(?:edu|sch|school|k12)(?:\.[a-z]{2,})+\b",
    "phone_numbers": r"(?<![\w.])\+?\d[\d ()-]{6,}\d(?!\w)",
}
PHONE_DIGITS = 8

MAGIC = (7, 77, 177)                      # first pixel of every blank picture
ID_LETTERS = "ABCDEFGHIJKLMNOPRSTUVWXYZ"  # no Q: Q is the padding
TOKEN_RE = re.compile(r"QZ[A-PR-Z]+Q*")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".gif", ".bmp", ".tif", ".tiff", ".webp",
             ".emf", ".wmf", ".svg", ".wdp", ".heic", ".heif"}
XML_EXT = (".xml", ".rels", ".vml")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
# (paragraph, its text nodes). Word cuts sentences into runs, so a name can be split
# across text nodes; matching is done on the joined paragraph. The DrawingML pair
# covers text in shapes, charts and SmartArt.
TEXT_GROUPS = [(f"{{{W}}}p", f"{{{W}}}t"), (f"{{{A}}}p", f"{{{A}}}t")]
GROUP_TEXT_TAGS = {t for _, t in TEXT_GROUPS}
WHOLE_META = {"creator", "lastModifiedBy", "Company", "Manager"}
PERSON_ATTRS = {"author", "initials", "userId", "providerId"}
PERSON_ELEMENTS = {"author", "person"}
PARSER = etree.XMLParser(resolve_entities=False, huge_tree=True)


def load_config() -> dict:
    if not CONFIG.exists():
        HOME.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        raise SystemExit(f"Created {CONFIG}. Put the school's name in it with a text editor "
                         "(not through Claude), then run again.")
    cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["terms"] = [t.strip() for t in cfg.get("terms", []) if t.strip()]
    if not cfg["terms"]:
        raise SystemExit(f"No terms in {CONFIG}.")
    if any(len(t) < 3 for t in cfg["terms"]):
        raise SystemExit(f"A term in {CONFIG} is shorter than 3 characters; remove it.")
    return cfg


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Vault:
    """The private record of what was swapped out."""

    def __init__(self):
        IMAGES.mkdir(parents=True, exist_ok=True)
        self.data = (json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists()
                     else {"texts": {}, "images": {}})
        self.by_token = {v: k for k, v in self.data["texts"].items()}
        self.by_blank = {v["blank_sha256"]: k for k, v in self.data["images"].items()}
        self._rx = None

    def save(self):
        tmp = MANIFEST.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(MANIFEST)

    # ---- text
    def token_for(self, original: str) -> str:
        if original in self.data["texts"]:
            return self.data["texts"][original]
        n, ident = len(self.data["texts"]), ""
        while True:
            ident = ID_LETTERS[n % 25] + ident
            n //= 25
            if n == 0:
                break
        token = "QZ" + ident
        if len(token) > len(original):
            raise ValueError("too many different short names to give each a same-length placeholder")
        token += "Q" * (len(original) - len(token))
        self.data["texts"][original] = token
        self.by_token[token] = original
        self._rx = None
        return token

    def untokenise(self, text: str, counts: dict) -> str:
        if not text or "QZ" not in text or not self.by_token:
            return text
        if self._rx is None:
            self._rx = re.compile("|".join(re.escape(t) for t in
                                           sorted(self.by_token, key=len, reverse=True)))

        def sub(m):
            counts["text"] += 1
            return self.by_token[m.group()]
        return self._rx.sub(sub, text)

    # ---- pictures
    def blank_for(self, data: bytes, name: str, source: str) -> bytes:
        """Keep the original picture in the vault; return a same-size white one."""
        ext = Path(name).suffix.lower()
        img_id = len(self.data["images"]) + 1
        (IMAGES / f"{img_id}{ext}").write_bytes(data)
        blank, size = make_blank(data, ext, img_id)
        self.data["images"][str(img_id)] = {"file": f"{img_id}{ext}", "size": size,
                                            "blank_sha256": sha(blank), "from": source}
        self.by_blank[sha(blank)] = str(img_id)
        return blank

    def blank_id(self, data: bytes) -> str | None:
        """Which vault picture a blank stands for: by exact bytes, else by its pixel code."""
        return self.by_blank.get(sha(data)) or blank_id_from_bytes(data)

    def original_image(self, img_id: str) -> bytes:
        return (IMAGES / self.data["images"][img_id]["file"]).read_bytes()


def make_blank(data: bytes, ext: str, img_id: int) -> tuple[bytes, list[int]]:
    if ext == ".svg":
        m = re.search(rb'width="([\d.]+)[a-z]*"[^>]*height="([\d.]+)[a-z]*"', data)
        w, h = (int(float(m.group(1))), int(float(m.group(2)))) if m else (100, 100)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
               f'<desc>blank picture {img_id}</desc><rect width="100%" height="100%" fill="white"/></svg>')
        return svg.encode(), [w, h]
    try:
        size = Image.open(io.BytesIO(data)).size  # header only; works for EMF/WMF too
    except Exception:
        size = (100, 100)
    fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG", ".gif": "GIF", ".bmp": "BMP",
           ".tif": "TIFF", ".tiff": "TIFF", ".webp": "WEBP"}.get(ext, "PNG")
    w, h = max(1, size[0]), max(1, size[1])
    img = Image.new("RGB", (w, h), "white")
    if w * h >= 2:
        img.putpixel((0, 0), MAGIC)
        img.putpixel((1, 0) if w > 1 else (0, 1),
                     ((img_id >> 16) & 255, (img_id >> 8) & 255, img_id & 255))
    buf = io.BytesIO()
    img.save(buf, fmt, **({"lossless": True} if fmt == "WEBP" else {}))
    return buf.getvalue(), [w, h]


def blank_id_from_bytes(data: bytes) -> str | None:
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    w, h = img.size
    if w * h < 2 or img.getpixel((0, 0)) != MAGIC:
        return None
    r, g, b = img.getpixel((1, 0) if w > 1 else (0, 1))
    return str((r << 16) | (g << 8) | b)


class Finder:
    """Swaps confidential strings for placeholders; with no vault, only records them."""

    def __init__(self, cfg: dict, vault: Vault | None):
        self.vault = vault
        self.rules = [(k, re.compile(v, re.I)) for k, v in PATTERNS.items() if cfg.get(k, True)]
        self.rules += [("terms", re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.I))
                       for t in sorted(cfg["terms"], key=len, reverse=True)]
        self.counts = {"text": 0, "pictures": 0}
        self.found: list[str] = []  # rule names hit, check mode only

    def tokenise(self, text: str, phones=True, terms_only=False) -> str:
        for name, rx in self.rules:
            if (terms_only and name != "terms") or (name == "phone_numbers" and not phones):
                continue

            def sub(m, name=name):
                s = m.group()
                if name == "phone_numbers" and sum(c.isdigit() for c in s) < PHONE_DIGITS:
                    return s
                if self.vault is None:
                    self.found.append(name)
                    return s
                self.counts["text"] += 1
                return self.vault.token_for(s)
            text = rx.sub(sub, text)
        return text

    def text_fn(self, text: str, kind: str) -> str:
        if kind == "text":
            return self.tokenise(text)
        if kind == "leaf":
            return self.tokenise(text, phones=False)
        if kind == "attr":
            return self.tokenise(text, terms_only=True)
        # "whole": author names and external link targets go entirely, listed or not.
        if self.vault is None:
            return self.tokenise(text, phones=False)
        if len(text) < 3 or TOKEN_RE.fullmatch(text):
            return text
        self.counts["text"] += 1
        return self.vault.token_for(text)


def local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def own_text_nodes(container, ctag, ttag):
    for t in container.iter(ttag):
        if next(t.iterancestors(ctag), None) is container:  # text boxes nest paragraphs
            yield t


def xml_transform(data: bytes, part: str, fn) -> bytes:
    """Run fn(text, kind) over every piece of text in one XML part. fn keeps lengths."""
    root = etree.fromstring(data, PARSER)
    for ctag, ttag in TEXT_GROUPS:
        for c in list(root.iter(ctag)):
            nodes = list(own_text_nodes(c, ctag, ttag))
            texts = [n.text or "" for n in nodes]
            joined = "".join(texts)
            new = fn(joined, "text") if joined else joined
            if new != joined:
                pos = 0  # same length, so each run gets back exactly its own slice
                for n, t in zip(nodes, texts):
                    n.text = new[pos:pos + len(t)]
                    pos += len(t)
                    n.set(XML_SPACE, "preserve")
    is_meta = part.startswith("docProps/")
    for el in root.iter():
        name = local(el.tag)
        if not name:
            continue
        # Other text: tracked deletions, field codes, document properties, chart values.
        if el.text and len(el) == 0 and el.tag not in GROUP_TEXT_TAGS:
            el.text = fn(el.text, "whole" if is_meta and name in WHOLE_META else "leaf")
        for key, val in list(el.attrib.items()):
            k = local(key)
            if (k in PERSON_ATTRS or (name in PERSON_ELEMENTS and k == "name")
                    or (name == "Relationship" and k == "Target" and el.get("TargetMode") == "External")):
                kind = "whole"
            else:
                kind = "attr"
            new = fn(val, kind)
            if new != val:
                el.set(key, new)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def docx_transform(src: Path, dst, text_fn, image_fn) -> list[str]:
    """Copy a .docx, passing its text through text_fn and its pictures through image_fn."""
    flags = []
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            n = info.filename
            data = zin.read(info)
            if Path(n).suffix.lower() in IMAGE_EXT:
                data = image_fn(data, n)
            elif n.endswith(XML_EXT):
                data = xml_transform(data, n, text_fn)
            elif "/embeddings/" in n or n.endswith(".bin"):
                flags.append("contains embedded objects or macros this script cannot clean")
            elif n.lower().endswith((".mp4", ".mov", ".m4a", ".mp3", ".wav", ".wmv", ".avi")):
                flags.append("contains audio or video this script cannot clean")
            zout.writestr(info, data)
    return list(dict.fromkeys(flags))
