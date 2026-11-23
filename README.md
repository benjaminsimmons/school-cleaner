# School Cleaner

Hides a school's details in Word documents before an AI assistant such as Claude sees them,
then puts them back into the finished work.

**Use it:** https://benjaminsimmons.github.io/school-cleaner/ (it can be installed as an app
from the browser's menu, and works offline).

## Privacy

The page is a progressive web app (PWA): plain HTML and JavaScript that run entirely in your
browser. There is no server. Documents are opened, cleaned and saved on your own computer and are
never uploaded; GitHub Pages only delivers the page's code. The school details and the original
pictures are kept in the browser's own storage (IndexedDB) on that computer.

## How it works

| | Text: the school's name and other listed details, email addresses, web addresses, phone numbers, comment and tracked-change authors | Pictures you choose |
|---|---|---|
| Cleaning | replaced by a placeholder of exactly the same length, e.g. `Hollybrook` becomes `QZAQQQQQQQ`; the same text always gets the same placeholder | stored in the browser; replaced by a white picture of the same pixel size in the same place |
| Putting back | every placeholder turned back into the original | every white picture swapped back, even one copied into a new document (it carries its number in its first two pixels) |

It reaches body text, headers, footers, footnotes, comments, tracked changes, text boxes, links,
document properties and file names, and keeps every piece of text's formatting. Only Word
`.docx` files are cleaned.

## Using it

1. **School details to hide**: type the school's name every way it is written, its address and
   staff names. Save.
2. **Clean new documents**: drop in the Word documents. Every picture is shown; click the ones to
   keep visible (all start as Hide). Clean, then read each cleaned document: names that are not
   in the list (pupils, other staff) are not caught.
3. Give the AI only the cleaned documents, and ask it to save its work as Word documents.
4. **Put the details back**: drop in what the AI produced. Save the results, and don't show them
   to the AI.
5. **Keep a backup** after cleaning. If the browser's data is cleared, the details can't be put
   back without one. The backup holds the real details, so keep it away from the AI.

## Limits

- Names that are not in the list are not caught.
- If the AI changes a placeholder (`QZAQQQQQQQ's` is fine, `QZAQQQQQQQs` is not), the document
  is marked "Check by hand"; search it for `QZ`.
- Embedded objects (a spreadsheet inside the document), macros, audio and video are flagged
  "Check by hand", not cleaned.
- Author initials shorter than 3 letters stay as they are.
- Word may show some placeholder pictures (GIF, BMP, EMF, WMF drawings) as broken boxes in the
  cleaned copy. Putting the details back restores them.

## Layout

```
web/       the PWA: index.html, page.js (the page), cleaner.js (cleaning and putting back),
           sw.js (offline), jszip.min.js (JSZip 3.10.1, MIT licence)
python/    the same tool as command-line scripts (sanitise.py, restore.py), with tests
.github/workflows/pages.yml   publishes web/ to GitHub Pages on every push to main
```

## Testing

Python scripts:

```bash
cd python
uv run --python 3.12 --with pytest --with "python-docx>=1.2" --with lxml --with pillow pytest tests
```

The web page was tested by hand in a browser on an invented school's document: cleaning it,
editing it as an AI would, making a new document from it, and putting the details back. Text,
formatting runs and pictures came back byte for byte.
