"""
Guards for a public repository: no personal paths, addresses, keys or
trademarked art sneak in.

The generic patterns below always run (and CI runs them). Anyone can add their
own words, such as a family name or a username, to
tests/personal_strings.local.txt (one per line, git-ignored), and the same
sweep checks for those on their machine.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"site", ".git", "__pycache__", "demo_data", "data", "logs", "personal", "generated", ".venv", ".pytest_cache", "Pending Deletion"}
TEXT_EXT = {".py", ".js", ".css", ".json", ".md", ".txt", ".html", ".cs", ".ps1", ".yml", ".yaml", ".cfg", ".toml", ".bat", ".gitignore"}

GENERIC = [
    (re.compile(r"[A-Za-z]:\\Users\\(?!\w*<|Public|Default)\w+", re.I), "a Windows user-profile path"),
    (re.compile(r"/Users/(?!Shared)\w+/", re.I), "a macOS home path"),
    (re.compile(r"/home/(?!runner/)\w+/"), "a Linux home path"),
    (re.compile(r"[\w.+-]+@(?!example\.)[\w-]+\.[a-z]{2,}", re.I), "an email address"),
    (re.compile(r"claude\.ai/(?:code/)?artifact"), "a private artifact link"),
    (re.compile(r"\b[0-9a-f]{32}\b"), "something shaped like a 32-hex API key"),
    (re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"), "a hard-coded credential"),
]
# Files where a hex id is legitimate (TMDB-style ids in samples, hashes in docs).
HEX_OK = {"CONFIG.md", "ARCHITECTURE.md", "README.md", "test_repo_hygiene.py", "test_setup_wizard.py", "test_pipeline_end_to_end.py"}

BANNED_ASSET_NAMES = re.compile(r"(?i)(marvel|tardis|banner_option|infinity)")


def _files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def _text(path):
    if os.path.splitext(path)[1].lower() not in TEXT_EXT and os.path.basename(path) != ".gitignore":
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _local_words():
    path = os.path.join(ROOT, "tests", "personal_strings.local.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return [w.strip() for w in f if w.strip() and not w.startswith("#")]
    except OSError:
        return []


def test_no_personal_paths_addresses_or_credentials():
    problems = []
    for path in _files():
        text = _text(path)
        if text is None or os.path.basename(path) == "test_repo_hygiene.py":
            continue
        for rx, what in GENERIC:
            if what.startswith("something shaped") and os.path.basename(path) in HEX_OK:
                continue
            for m in rx.finditer(text):
                problems.append(f"{os.path.relpath(path, ROOT)}: {what}: {m.group(0)[:60]}")
    assert not problems, "\n".join(problems[:40])


def test_none_of_the_local_personal_words_appear():
    words = _local_words()
    if not words:
        pytest.skip("no tests/personal_strings.local.txt on this machine")
    problems = []
    for path in _files():
        if os.path.basename(path) in ("personal_strings.local.txt", "test_repo_hygiene.py"):
            continue
        text = _text(path)
        if text is None:
            continue
        low = text.lower()
        for w in words:
            if w.lower() in low:
                problems.append(f"{os.path.relpath(path, ROOT)}: contains {w!r}")
    assert not problems, "\n".join(problems[:40])


def test_no_branded_or_oversized_images_are_shipped():
    problems = []
    for path in _files():
        rel = os.path.relpath(path, ROOT)
        if os.path.splitext(path)[1].lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}:
            if BANNED_ASSET_NAMES.search(os.path.basename(path)):
                problems.append(f"{rel}: looks like branded art")
            limit = 5 * 1024 * 1024 if rel.replace(os.sep, "/").startswith("docs/") and path.endswith(".gif") else 600 * 1024
            if os.path.getsize(path) > limit:
                problems.append(f"{rel}: over {limit // 1024} KB")
    assert not problems, "\n".join(problems)


def test_compiled_binaries_are_not_checked_in():
    exes = [os.path.relpath(p, ROOT) for p in _files() if p.lower().endswith((".exe", ".dll", ".pyc"))]
    assert not exes, exes


def test_license_and_attribution_files_exist():
    for f in ("LICENSE", "NOTICE.md"):
        assert os.path.isfile(os.path.join(ROOT, f)), f + " is missing"
    text = open(os.path.join(ROOT, "NOTICE.md"), encoding="utf-8").read()
    assert "TMDB" in text and "Orbitron" in text


BINARY_OK = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff2", ".mp4", ".example"}


def test_only_expected_file_types_are_present():
    """A stray cache, database or coverage file can hold local paths; only text and known media may live here."""
    odd = []
    for path in _files():
        ext = os.path.splitext(path)[1].lower()
        name = os.path.basename(path)
        if ext in TEXT_EXT or ext in BINARY_OK or name in (".gitignore", ".gitattributes", ".editorconfig", "LICENSE"):
            continue
        odd.append(os.path.relpath(path, ROOT))
    assert not odd, "unexpected files (add to .gitignore or the allow-list): " + ", ".join(odd[:20])
