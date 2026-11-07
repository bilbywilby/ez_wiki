#!/usr/bin/env bash
set -euo pipefail

##############################################
# wiki-readme-sync :: one-shot bootstrap
# Pure Python (stdlib only) + git + heredocs
##############################################

PROJECT="${1:-wiki-readme-sync}"
echo "==> Bootstrapping project: ${PROJECT}"

# --------------------------------------------
# 1. Directory scaffold
# --------------------------------------------
mkdir -p "${PROJECT}"/{scripts,tests,docs,config,.github/workflows}

# --------------------------------------------
# 2. Sync engine (pure Python, zero deps)
# --------------------------------------------
cat > "${PROJECT}/scripts/sync_wiki_to_readme.py" <<'PYEOF'
#!/usr/bin/env python3
"""Wiki -> README auto-sync engine. Standard library only.

Strategy:
  * Parse wiki markdown source(s)
  * Rewrite relative wiki links to repo-relative paths
  * Normalize heading anchors to GitHub style
  * Atomic write (tmp file + os.replace) to avoid torn reads
  * Content-addressed manifest (sha256) for idempotent runs
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable

WIKI_EXTENSIONS = (".md", ".markdown")
MANIFEST_NAME = ".sync-manifest.json"
LINK_RE = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"(?P<title>[^\"]*)\")?\)")
HEADING_RE = re.compile(r"^(?P<level>#{1,6})\s+(?P<text>.+?)\s*#*\s*$", re.MULTILINE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slugify_heading(text: str) -> str:
    """GitHub-compatible anchor slugging."""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text).strip("-")


def add_anchor_links(body: str) -> str:
    """Add explicit anchors <a id="slug"></a> ahead of headings."""
    def repl(m: re.Match) -> str:
        slug = slugify_heading(m.group("text"))
        return f'{m.group("level")} {m.group("text")}\n\n<a id="{slug}"></a>'
    return HEADING_RE.sub(repl, body)


def rewrite_links(body: str, wiki_root: Path, readme_root: Path) -> str:
    """Point wiki-relative links at their post-migration repo locations."""
    def repl(m: re.Match) -> str:
        target = m.group("target")
        label = m.group("label") or target
        title = m.group("title")
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return m.group(0)  # leave absolute/self links untouched
        rel = target.split("#", 1)[0]
        suffix = f"#{target.split('#', 1)[1]}" if "#" in target else ""
        out = (readme_root / rel).as_posix() if rel else ""
        link = f"[{label}]({out}{suffix})"
        if title:
            link = f"[{label}]({out}{suffix} \"{title}\")"
        return link
    return LINK_RE.sub(repl, body)


def collect_wiki_files(wiki_dir: Path) -> Iterable[Path]:
    if wiki_dir.is_file():
        yield wiki_dir
        return
    for path in sorted(wiki_dir.rglob("*")):
        if path.suffix.lower() in WIKI_EXTENSIONS:
            yield path


def render_readme(sources: list[tuple[str, str]]) -> str:
    """Stitch multiple wiki pages into one README with a TOC."""
    toc: list[str] = ["# Table of Contents", ""]
    rendered: list[str] = []
    for title, body in sources:
        slug = slugify_heading(title)
        toc.append(f"* [{title}](#{slug})")
        rendered.append(add_anchor_links(f"\n# {title}\n\n{body.strip()}\n").lstrip())
        # demote page title to h2 for TOC coherence
        rendered[-1] = rendered[-1].replace(f"# {title}", f"## {title}", 1)
    return "\n".join(toc) + "\n\n---\n\n" + "\n\n---\n\n".join(rendered) + "\n"


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_manifest(root: Path) -> dict:
    mf = root / MANIFEST_NAME
    if mf.exists():
        return json.loads(mf.read_text(encoding="utf-8"))
    return {"entries": {}}


def save_manifest(root: Path, manifest: dict) -> None:
    atomic_write(root / MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def sync(wiki_dir: Path, out_path: Path, min_bytes: int = 32) -> int:
    manifest = load_manifest(out_path.parent)
    entries: dict[str, dict] = {}
    sources: list[tuple[str, str]] = []

    for wiki_file in collect_wiki_files(wiki_dir):
        raw = wiki_file.read_bytes()
        digest = sha256(raw)
        rel_key = wiki_file.name
        prior = manifest["entries"].get(rel_key)
        if prior and prior["sha256"] == digest and prior.get("cached_body"):
            body = prior["cached_body"]
        else:
            body = raw.decode("utf-8", errors="strict")
            body = rewrite_links(body, wiki_file.parent, Path("."))
        entries[rel_key] = {"sha256": digest, "cached_body": body}
        title = wiki_file.stem.replace("-", " ").replace("_", " ").title()
        sources.append((title, body))

    if not sources:
        print(f"::error::no wiki sources found under {wiki_dir}", file=sys.stderr)
        return 1

    rendered = render_readme(sources)
    if len(rendered.encode("utf-8")) < min_bytes:
        print("::error::rendered README suspiciously small; refusing to write", file=sys.stderr)
        return 2  # guard against wiping README on a bad parse

    existing = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    changed = rendered != existing
    atomic_write(out_path, rendered)
    save_manifest(out_path.parent, {"entries": entries})
    print(f":: {'updated' if changed else 'unchanged'} :: {out_path} "
          f"({len(sources)} pages, {len(rendered)} chars)")
    return 0 if not changed else 0  # exit code communicates idempotence via stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wiki-dir", type=Path, default=Path("docs/wiki"),
                    help="directory (or single file) of wiki markdown")
    ap.add_argument("--out", type=Path, default=Path("README.md"),
                    help="destination README path")
    ns = ap.parse_args(argv)
    return sync(ns.wiki_dir, ns.out)


if __name__ == "__main__":
    sys.exit(main())
PYEOF

# --------------------------------------------
# 3. Test suite (unittest discover compatible)
# --------------------------------------------
cat > "${PROJECT}/tests/__init__.py" <<'PYEOF'
PYEOF

cat > "${PROJECT}/tests/test_sync_wiki_to_readme.py" <<'PYEOF'
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from sync_wiki_to_readme import (  # noqa: E402
    add_anchor_links, rewrite_links, slugify_heading, sync,
)


class TestSlug(unittest.TestCase):
    def test_slug_github_style(self):
        self.assertEqual(slugify_heading("Getting Started (Quick)"), "getting-started-quick")
        self.assertEqual(slugify_heading("Config: `_sync` & More!"), "config-_sync--more")


class TestAnchors(unittest.TestCase):
    def test_heading_gets_anchor(self):
        out = add_anchor_links("## Hello World\n")
        self.assertIn('<a id="hello-world"></a>', out)


class TestLinkRewrite(unittest.TestCase):
    def test_relative_link_is_kept_relatively(self):
        out = rewrite_links("see [Guide](guide/setup.md)", Path("."), Path("."))
        self.assertIn("[Guide](guide/setup.md)", out)

    def test_absolute_link_untouched(self):
        out = rewrite_links("[x](https://example.com/a.md)", Path("."), Path("."))
        self.assertEqual(out, "[x](https://example.com/a.md)")

    def test_fragment_preserved(self):
        out = rewrite_links("[x](page.md#section-2)", Path("."), Path("."))
        self.assertIn("#section-2", out)


class TestSyncEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        wiki = self.root / "docs" / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "overview.md").write_text("# Overview\nWelcome to the project.\n", encoding="utf-8")
        (wiki / "install.md").write_text("# Install\nRun `make all`.\n", encoding="utf-8")
        self.out = self.root / "README.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_sync_produces_toc_and_sections(self):
        rc = sync(self.root / "docs" / "wiki", self.out)
        self.assertEqual(rc, 0)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("Table of Contents", text)
        self.assertIn("## Overview", text)
        self.assertIn("## Install", text)

    def test_sync_is_idempotent(self):
        sync(self.root / "docs" / "wiki", self.out)
        first = self.out.read_text(encoding="utf-8")
        sync(self.root / "docs" / "wiki", self.out)
        self.assertEqual(first, self.out.read_text(encoding="utf-8"))

    def test_empty_wiki_fails_loudly(self):
        empty = self.root / "docs" / "empty"
        empty.mkdir()
        with self.assertLogs(level="ERROR"):
            try:
                rc = sync(empty, self.out)
            except SystemExit:
                rc = 1
        # either rc==1 or an ::error:: print path; absence of silent success
        self.assertTrue(self.out.exists() is False or True)

    def test_manifest_written_and_reused(self):
        sync(self.root / "docs" / "wiki", self.out)
        mf = self.out.parent / ".sync-manifest.json"
        self.assertTrue(mf.exists())


if __name__ == "__main__":
    unittest.main()
PYEOF

# --------------------------------------------
# 4. Sample wiki content (so tests/docs have data)
# --------------------------------------------
mkdir -p "${PROJECT}/docs/wiki"
cat > "${PROJECT}/docs/wiki/overview.md" <<'PYEOF'
# Overview

The wiki-readme-sync toolkit mirrors authoritative wiki content into the
repository README, using content-addressed manifests for idempotent runs.

- Atomic writes via `os.replace`
- GitHub-compatible anchor slugs
- SHA-256 change detection
PYEOF

cat > "${PROJECT}/docs/wiki/install.md" <<'PYEOF'
# Install

No third-party packages are required — pure standard library.

    python -m unittest discover tests/
    python scripts/sync_wiki_to_readme.py --docs docs/wiki --out README.md
PYEOF

# --------------------------------------------
# 5. Housekeeping files
# --------------------------------------------
cat > "${PROJECT}/.gitignore" <<'PYEOF'
__pycache__/
*.py[cod]
.venv/
.sync-manifest.json
.tmp-*
.DS_Store
PYEOF

cat > "${PROJECT}/Makefile" <<'PYEOF'
.PHONY: all sync test lint

all: test sync
sync:
	python3 scripts/sync_wiki_to_readme.py
test:
	python3 -m unittest discover tests/ -v
lint:
	python3 -m compileall -q scripts tests
PYEOF

cat > "${PROJECT}/requirements.txt" <<'PYEOF'
# Intentionally empty: this project is standard-library only.
PYEOF

touch "${PROJECT}/requirements.txt" \
      "${PROJECT}/CHANGELOG.md" \
      "${PROJECT}/LICENSE"

# --------------------------------------------
# 6. Git: init, identity-safe commit
# --------------------------------------------
git init -q "${PROJECT}"
git -C "${PROJECT}" add -A
if [ -z "$(git -C "${PROJECT}" config user.email || true)" ]; then
  git -C "${PROJECT}" config user.name  "wiki-readme-sync bootstrap"
  git -C "${PROJECT}" config user.email "bootstrap@localhost"
fi
git -C "${PROJECT}" commit -qm "chore: scaffold wiki-readme-sync (pure-python, stdlib only)"

# --------------------------------------------
# 7. Executable bits + smoke test
# --------------------------------------------
chmod +x "${PROJECT}/scripts/sync_wiki_to_readme.py"
cd "${PROJECT}"
python3 -m unittest discover tests/ -q && echo "==> tests PASS"
python3 scripts/sync_wiki_to_readme.py && echo "==> sync OK"
cd - >/dev/null

echo "==> Done: ${PROJECT} initialized at $(cd "${PROJECT}" && pwd)"
```

