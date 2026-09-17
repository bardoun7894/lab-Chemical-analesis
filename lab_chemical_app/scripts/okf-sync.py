#!/usr/bin/env python3
"""
okf-sync.py — Project KB → OKF projection layer (v3).

Generates an agent-optimized knowledge directory (okf/) from the existing
ai-knowledge-base KB. The OKF layer is READ-ONLY — never modify KB files here.

OKF v3 improvements:
  - [[wikilinks]] converted to standard markdown links → agents can navigate
    the knowledge graph without custom wikilink resolution
  - resource: field in frontmatter linking back to the KB source of truth
  - Progressive index.md with type-grouped sections + agent guidance
  - llms.txt at project root advertising the OKF bundle
  - summary.md — compact RAG-ready listing for fast agent scanning
  - Memory bridge: includes raw/memory-sync/ in the OKF projection

Usage:
    uv run python scripts/okf-sync.py                     # dry-run (preview)
    uv run python scripts/okf-sync.py --apply              # actually generate okf/
    uv run python scripts/okf-sync.py --force              # force regenerate everything
    uv run python scripts/okf-sync.py --quiet              # suppress output (for auto-sync)

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

# ── Directories to mirror ───────────────────────────────────────────
KB_SUBDIRS = {"concepts", "connections", "sources", "entities", "qa"}
MEMORY_SYNC_DIR = "raw/memory-sync"

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ══════════════════════════════════════════════════════════════════════
#  Frontmatter parsing
# ══════════════════════════════════════════════════════════════════════


def parse_frontmatter(text: str) -> dict:
    result: dict = {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return result
    fm_text = m.group(1)
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m2 = re.match(r"^(\w+):\s*(.*)", line)
        if m2:
            key = m2.group(1)
            val = m2.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                if inner:
                    items = [item.strip().strip('"').strip("'") for item in re.split(r",\s*(?![^[]*\])", inner) if item.strip()]
                    val = items
                else:
                    val = []
            result[key] = val
    return result


def format_tags_for_index(tags) -> str:
    if not tags:
        return ""
    if isinstance(tags, list):
        return ", ".join(str(t) for t in tags)
    return str(tags)


def slug_to_readable(slug: str) -> str:
    return slug.replace("-", " ").title()


# ══════════════════════════════════════════════════════════════════════
#  OKF transformations
# ══════════════════════════════════════════════════════════════════════


def convert_wikilinks(text: str) -> str:
    """Convert [[wikilinks]] to standard markdown links.

    OKF spec: "links between concepts are normal markdown links; that link
    structure is what makes the bundle a knowledge graph."

    Handles:
    - [[type/slug]]          → [Readable Slug](type/slug.md)
    - [[type/slug|Display]]  → [Display](type/slug.md)
    - [[file.md]]            → [File](file.md)
    """
    def _replace(m: re.Match) -> str:
        content = m.group(1)
        parts = content.split("|", 1)
        target = parts[0].strip()
        display = parts[1].strip() if len(parts) > 1 else None

        target_path = target if target.endswith(".md") else target + ".md"

        if display:
            link_text = display
        else:
            slug = target.split("/")[-1].replace("-", " ").title()
            link_text = slug

        return f"[{link_text}]({target_path})"

    return WIKILINK_RE.sub(_replace, text)


def add_resource_frontmatter(text: str, subdir: str, filename: str) -> str:
    """Add `resource:` field to frontmatter linking back to KB source."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    fm, body = parts[1], parts[2]
    if re.search(r"^resource:", fm, re.MULTILINE):
        return text
    resource = f"knowledge/{subdir}/{filename}"
    fm_stripped = fm.rstrip()
    new_fm = fm_stripped + f"\nresource: kb://{resource}\n"
    return f"---{new_fm}---{body}"


# ══════════════════════════════════════════════════════════════════════
#  OKF generation
# ══════════════════════════════════════════════════════════════════════


class OKFGenerator:
    """Generates OKF projection layer from a KB."""

    def __init__(self, root: Path, force: bool = False):
        self.root = root
        self.kb_root = root / "ai-knowledge-base" / "knowledge"
        self.okf_root = root / "okf"
        self.memory_root = root / "ai-knowledge-base" / MEMORY_SYNC_DIR
        self.force = force
        self.now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.index_entries: list[dict] = []

    def needs_sync(self) -> bool:
        if not self.okf_root.exists():
            return True
        if self.force:
            return True
        okf_index = self.okf_root / "index.md"
        if not okf_index.exists():
            return True
        okf_mtime = okf_index.stat().st_mtime
        for subdir in KB_SUBDIRS:
            kb_dir = self.kb_root / subdir
            if not kb_dir.is_dir():
                continue
            for f in kb_dir.glob("*.md"):
                if f.stat().st_mtime > okf_mtime:
                    return True
        if self.memory_root.is_dir():
            for f in self.memory_root.glob("*.md"):
                if f.stat().st_mtime > okf_mtime:
                    return True
        return False

    def run(self, apply: bool) -> bool:
        """Run the sync. Returns True if changes are needed."""
        if not self.kb_root.is_dir():
            print(f"  ⚠️  No KB found at {self.kb_root}")
            return False
        if not self.needs_sync():
            print("  ✅ OKF is up-to-date. Nothing to sync.")
            return False

        changes = 0

        for subdir in sorted(KB_SUBDIRS):
            kb_dir = self.kb_root / subdir
            if not kb_dir.is_dir():
                continue
            okf_dir = self.okf_root / subdir
            for src in sorted(kb_dir.glob("*.md")):
                slug = src.stem
                text = src.read_text(encoding="utf-8")

                # OKF v3: resource provenance
                text = add_resource_frontmatter(text, subdir, src.name)

                # OKF v3: wikilink → markdown link conversion
                text = convert_wikilinks(text)

                fm = parse_frontmatter(text)
                name = fm.get("name", slug_to_readable(slug))
                art_type = fm.get("type", subdir.rstrip("s"))
                tags = fm.get("tags", [])

                self.index_entries.append({
                    "name": name, "type": art_type, "tags": tags,
                    "summary": fm.get("description", ""),
                    "updated": fm.get("updated", ""),
                    "src": f"{subdir}/{src.name}",
                })

                if apply:
                    okf_dir.mkdir(parents=True, exist_ok=True)
                    (okf_dir / src.name).write_text(text, encoding="utf-8")
                    changes += 1
                else:
                    changes += 1

        # Memory bridge
        if self.memory_root.is_dir():
            mem_okf_dir = self.okf_root / "memory"
            for src in sorted(self.memory_root.glob("*.md")):
                text = src.read_text(encoding="utf-8")
                fm = parse_frontmatter(text)
                name = fm.get("title", src.stem.replace("-", " ").title())
                art_type = "memory-sync"
                tags = fm.get("tags", [])

                self.index_entries.append({
                    "name": name, "type": art_type, "tags": tags,
                    "summary": fm.get("description", ""),
                    "updated": fm.get("updated", ""),
                    "src": f"memory/{src.name}",
                })

                if apply:
                    mem_okf_dir.mkdir(parents=True, exist_ok=True)
                    (mem_okf_dir / src.name).write_text(text, encoding="utf-8")
                    changes += 1
                else:
                    changes += 1

        self._generate_index(apply)
        self._generate_summary(apply)
        self._generate_log(apply, changes)
        self._generate_llmstxt(apply)
        return changes > 0

    def _generate_llmstxt(self, apply: bool):
        """Generate llms.txt at project root advertising the OKF bundle."""
        type_counts: dict[str, int] = {}
        for entry in self.index_entries:
            type_counts[entry["type"]] = type_counts.get(entry["type"], 0) + 1

        lines = [
            "# OKF Knowledge Bundle",
            "",
            f"Auto-generated {self.now.split()[0]}. Project: {self.root.name}",
            "",
            "This project's agent-ready knowledge is available in the OKF bundle:",
            "",
            "- [OKF Index](okf/index.md) — Progressive disclosure with type-grouped sections",
            "- [OKF Summary](okf/summary.md) — Compact RAG-ready one-line-per-article listing",
            "- [OKF Log](okf/log.md) — Sync history and change tracking",
            "",
            "Types available:",
        ]
        for t in sorted(type_counts):
            lines.append(f"- {self._plural_type(t)}: {type_counts[t]} file(s)")
        lines.append("")
        lines.append("Start with okf/index.md for progressive knowledge discovery.")
        lines.append("")

        if apply:
            (self.root / "llms.txt").write_text("\n".join(lines), encoding="utf-8")

    def _generate_index(self, apply: bool):
        """Progressive-disclosure index.md: type-grouped sections + agent guidance.

        OKF spec calls for a "directory listing / progressive disclosure" entry
        point that tells agents what's available and how to navigate.
        """
        type_groups: dict[str, list[dict]] = {}
        for entry in self.index_entries:
            type_groups.setdefault(entry["type"], []).append(entry)

        total = len(self.index_entries)

        lines = [
            "# OKF Index — Agent-Ready Knowledge Bundle",
            "",
            f"Auto-generated from `ai-knowledge-base/knowledge/` on {self.now}.",
            "",
            "## Quick Start for Agents",
            "",
            f"This bundle contains **{total} articles** across {len(type_groups)} type(s):",
        ]
        for t in sorted(type_groups):
            lines.append(f"- **{self._plural_type(t)}**: {len(type_groups[t])} article(s)")
        lines.extend([
            "",
            "**How to navigate:**",
            "1. Browse a type section below based on your query.",
            "2. Read specific files for details.",
            "3. Follow markdown links between articles for related knowledge.",
            "4. See [summary.md](summary.md) for a compact one-line-per-article listing.",
            "",
            "---",
            "",
        ])

        for t in sorted(type_groups):
            entries = sorted(type_groups[t], key=lambda e: e["name"])
            lines.append(f"## {self._plural_type(t)} ({len(entries)})")
            lines.append("")
            for entry in entries:
                summary = entry["summary"]
                src = entry["src"]
                if summary:
                    if len(summary) > 100:
                        summary = summary[:97] + "..."
                    lines.append(f"- [{entry['name']}]({src}) — {summary}")
                else:
                    lines.append(f"- [{entry['name']}]({src})")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("See [summary.md](summary.md) for a compact RAG-ready listing.")
        lines.append("")

        if apply:
            self.okf_root.mkdir(parents=True, exist_ok=True)
            (self.okf_root / "index.md").write_text("\n".join(lines), encoding="utf-8")

    def _generate_summary(self, apply: bool):
        """RAG-ready summary.md: one line per article."""
        type_counts: dict[str, int] = {}
        for entry in self.index_entries:
            type_counts[entry["type"]] = type_counts.get(entry["type"], 0) + 1

        lines = [
            "# OKF Summary — Agent Knowledge Listing",
            "", f"Auto-generated {self.now}.", "",
            "## Stats", "| Type | Count |", "|------|-------|",
        ]
        for t in sorted(type_counts):
            lines.append(f"| {self._plural_type(t)} | {type_counts[t]} |")
        lines.extend(["", "## Articles", ""])

        for entry in sorted(self.index_entries, key=lambda e: (e["type"], e["name"])):
            tags_part = f" [{format_tags_for_index(entry['tags'])}]" if entry["tags"] else ""
            lines.append(f"- `{entry['type']}` **{entry['name']}** → {entry['src']}{tags_part}")
        lines.append("")

        if apply:
            self.okf_root.mkdir(parents=True, exist_ok=True)
            (self.okf_root / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    def _generate_log(self, apply: bool, count: int):
        type_counts: dict[str, int] = {}
        for entry in self.index_entries:
            type_counts[entry["type"]] = type_counts.get(entry["type"], 0) + 1

        lines = [
            "# OKF Sync Log",
            "",
            f"## {self.now.split()[0]} — Sync #{count} files",
            "",
            f"- **Source**: `ai-knowledge-base/knowledge/` + `raw/memory-sync/`",
            f"- **Files synced**: {count}",
            f"- OKF agent-ready index: `okf/index.md`",
            f"- OKF summary: `okf/summary.md`",
            f"- llms.txt: `{self.root.name}/llms.txt`",
            f"",
            f"### Summary", "", "| Type | Count |", "|------|-------|",
        ]
        for t in sorted(type_counts):
            lines.append(f"| {self._plural_type(t)} | {type_counts[t]} |")
        lines.append("")

        if apply:
            self.okf_root.mkdir(parents=True, exist_ok=True)
            (self.okf_root / "log.md").write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _plural_type(t: str) -> str:
        plural_map = {
            "concept": "concepts", "connection": "connections",
            "source": "sources", "entity": "entities",
            "qa": "qa", "memory-sync": "memory-sync",
        }
        return plural_map.get(t, t + "s")

    def report(self, count: int):
        type_count: dict[str, int] = {}
        for entry in self.index_entries:
            type_count[entry["type"]] = type_count.get(entry["type"], 0) + 1
        print(f"\n  📊 Projection summary:")
        for t in sorted(type_count):
            print(f"    {self._plural_type(t)}: {type_count[t]} files")
        print(f"    Total: {count} files")
        print(f"    Index: {self.okf_root}/index.md")
        print(f"    Summary: {self.okf_root}/summary.md")
        print(f"    llms.txt: {self.root}/llms.txt")


# ══════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="Generate OKF projection layer from KB.")
    parser.add_argument("--apply", action="store_true", help="Write okf/ directory")
    parser.add_argument("--force", action="store_true", help="Force regeneration")
    parser.add_argument("--quiet", action="store_true", help="Suppress output")
    parser.add_argument("--project", type=str, help="Project root (default: cwd)")
    args = parser.parse_args()

    if args.project:
        root = Path(args.project).resolve()
    else:
        root = Path.cwd().resolve()

    if not args.quiet:
        print(f"📁 Project: {root.name}")

    gen = OKFGenerator(root, force=args.force)
    if not gen.run(apply=False):
        if not gen.needs_sync():
            return

    dry_count = len(gen.index_entries)
    if dry_count > 0 and not args.quiet:
        gen.report(dry_count)

    if args.apply:
        gen2 = OKFGenerator(root, force=True)
        gen2.run(apply=True)
        actual = len(gen2.index_entries)
        if not args.quiet:
            print(f"\n  ✅ OKF layer written: okf/ ({actual} files)")
    else:
        if not args.quiet:
            print(f"\n  ⚠️  Dry-run only. Run with --apply to generate okf/ directory.")


if __name__ == "__main__":
    main()
