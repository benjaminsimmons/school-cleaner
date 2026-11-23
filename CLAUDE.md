# School Cleaner — project instructions

A PWA (`web/`) and matching Python scripts (`python/`) that hide a school's details in Word
documents before an AI reads them, and put them back afterwards. `README.md` says how it works.

## Backlog

The owner's backlog Google Sheet ("Ben's backlog", My Drive). Not a file in this repo.

## Rules for Claude

- **Never read real school documents, the browser's stored details, backups
  (`school-cleaner-backup-*.json`) or `~/.school-sanitiser/`.** Work only on documents the
  cleaner has already cleaned.
- **Keep every placeholder exactly as written** (`QZ` + capitals + `Q` padding), letter for
  letter, and leave the white pictures in place; copying them into new documents is fine.
  Save new work as `.docx`.
- **The page must never send data anywhere.** No analytics, no fonts or scripts from other
  sites, no network calls except loading its own files. Libraries are copied into `web/`.
- **Bump `VERSION` in `web/sw.js` with every change to `web/`**, or installed copies keep the
  old version.
- The web and Python versions use the same placeholder scheme; change both together.
- Tests use the invented "Hollybrook Primary School" and invented people only.
