# Contributing

Thanks for looking. A few things make a change easy to review.

## Set up

```bash
git clone <repo> && cd mediabox
pip install -r requirements-dev.txt
python run.py --demo          # a library to click around in
python -m pytest              # the whole suite
```

Browser tests are skipped unless Playwright and a browser are installed:

```bash
pip install playwright && python -m playwright install chromium
python -m pytest tests/e2e
```

## How the code is organised

Read [ARCHITECTURE.md](ARCHITECTURE.md) first. In one line: `pipeline/` scripts turn folders into
`data/media_db.json`, `app/render_engine.py` turns that into one page (`app/templates`, `app/static`), and
`app/web_server.py` serves it and does the things a page can't.

## Ground rules

- **Never delete, never overwrite.** Anything that touches the user's files must move, not remove, and must
  refuse to clobber. There are tests for this; add one when you add a code path.
- **Nothing personal in the repo.** No paths, names, keys, e-mail addresses, trademarked art.
  `tests/test_repo_hygiene.py` enforces the generic cases; add your own words to the git-ignored
  `tests/personal_strings.local.txt`.
- **Per-library facts are data, not code.** A fix for one person's collection belongs in `scan_overrides.json` or
  `library_rules.json`, not in a Python table.
- **Atomic writes.** Shared JSON is written to a temp file and swapped in (`pipeline/atomic_write.py`).
- **Escape what you embed.** File names and titles are user data. Use `_js_json` for JSON in the page and
  `html_escape` for HTML.
- **Windows-only code is gated** on `app_config.IS_WINDOWS` and lives in `extras/` where possible.

## Before you open a pull request

```bash
python -m pytest
python -m ruff check --select E9,F63,F7,F82 --target-version py311 .
```

If you change the UI, regenerate the screenshots (`python tools/capture_screenshots.py`) and, for a big change,
the tour (`python tools/record_demo.py`).
