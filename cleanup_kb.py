#!/usr/bin/env python3
"""Clean up crawled knowledge base for AI consumption.

Operations (in order):
1. Remove short files (<min_words after frontmatter)
2. Strip YAML frontmatter from all files
3. Strip blog/site footer noise (subscribe, related posts, etc.)
4. Fix collapsed code blocks (no newlines, >200 chars)
5. Remove near-duplicate files (keep longer version)
6. Clean up empty directories

Usage:
    python3 cleanup_kb.py ./claude-kb-clean --min-words 80 --dry-run
    python3 cleanup_kb.py ./claude-kb-clean --min-words 80
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

# ── Footer noise patterns ──────────────────────────────────────────────
# Each pattern matches a line that starts a footer block.
# Everything from the first match to EOF is removed.
FOOTER_BLOCK_PATTERNS = [
    re.compile(r"^#{1,4}\s*Related\s*Posts?\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*You might also like\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*Recommended\s*(Reading|Articles|Posts)?\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*Share this\s*(article|post|lesson)?\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*Comments?\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*Leave a (Reply|Comment)\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*Newsletter\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*About the Author\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*More from\s", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*Tags?\s*$", re.IGNORECASE),
    re.compile(r"^#{1,4}\s*Share\s*$", re.IGNORECASE),
]

# Single-line noise (removed individually, not block-truncated)
NOISE_LINE_PATTERNS = [
    re.compile(r"^Continue\s+[Rr]eading\s*\.{0,3}\s*$"),
    re.compile(r"^Share this (article|post|lesson)\s*$", re.IGNORECASE),
    re.compile(r"^Subscribe\s*$", re.IGNORECASE),
    re.compile(r"^\s*Sign up for (our|the|my) (newsletter|updates)", re.IGNORECASE),
    re.compile(r"^\s*Follow us on\s", re.IGNORECASE),
    re.compile(r"^\s*\d+,?\d*\+?\s*developers?\s*[·•]\s*Free", re.IGNORECASE),
    re.compile(r"^\s*Preview an issue\s*→", re.IGNORECASE),
    re.compile(r"^\[?\s*Share\s*\]?\s*\[?\s*Tweet\s*\]?", re.IGNORECASE),
    # Badge image URLs
    re.compile(r"^\s*!\[.*?\]\(https://img\.shields\.io/"),
    re.compile(r"^\s*!\[.*?\]\(https://badge"),
    # Empty link lists (nav remnants)
    re.compile(r"^\s*\[.*?\]\(#\)\s*$"),
    # Cookie/consent leftovers
    re.compile(r"^\s*(Accept|Reject)\s*(All\s*)?(Cookies?)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*We use cookies", re.IGNORECASE),
    re.compile(r"^\s*This (website|site) uses cookies", re.IGNORECASE),
]

FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)

# Code block collapse: a ``` block with a single very long line (likely collapsed code)
COLLAPSED_CODE_RE = re.compile(r"```(\w*)\n(.{200,})\n```")


def strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter block."""
    return FRONTMATTER_RE.sub("", content, count=1)


def strip_footer_noise(content: str) -> str:
    """Remove footer blocks and individual noise lines."""
    lines = content.split("\n")

    # Find first footer block start (only in last 30% of file)
    cutoff = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if any(p.match(stripped) for p in FOOTER_BLOCK_PATTERNS):
            if i > len(lines) * 0.7:
                cutoff = i
                break

    lines = lines[:cutoff]

    # Remove individual noise lines
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped and any(p.match(stripped) for p in NOISE_LINE_PATTERNS):
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


def fix_collapsed_code_blocks(content: str) -> tuple[str, int]:
    """Fix code blocks where all code is on a single very long line.

    Heuristic: if a fenced code block has one line >200 chars containing
    semicolons or braces, try to split it into readable lines.
    """
    fixes = 0

    def _split_code(m: re.Match) -> str:
        nonlocal fixes
        lang = m.group(1)
        code = m.group(2)

        # Only fix if it looks like collapsed code (has statement separators)
        if not any(c in code for c in (";", "{", "}")):
            return m.group(0)

        # Split on common statement boundaries
        # Insert newline after: ; } { (but not inside strings — best effort)
        fixed = code
        # Split after semicolons (not inside quotes — rough heuristic)
        fixed = re.sub(r";(?=[^\s])", ";\n", fixed)
        # Split after opening braces
        fixed = re.sub(r"\{(?=[^\s\}])", "{\n", fixed)
        # Split before closing braces
        fixed = re.sub(r"(?<=[^\s\{])\}", "\n}", fixed)

        if fixed != code:
            fixes += 1
            return f"```{lang}\n{fixed}\n```"
        return m.group(0)

    result = COLLAPSED_CODE_RE.sub(_split_code, content)
    return result, fixes


def clean_excessive_blank_lines(content: str) -> str:
    """Collapse 3+ consecutive blank lines into 2."""
    return re.sub(r"\n{4,}", "\n\n\n", content)


def word_count(text: str) -> int:
    return len(text.split())


def content_hash(text: str, n_lines: int = 10) -> str:
    """Hash first N non-empty content lines for near-duplicate detection."""
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()][:n_lines]
    return hashlib.md5("\n".join(lines).encode()).hexdigest()


def find_near_duplicates(files: dict[Path, str]) -> list[Path]:
    """Find near-duplicate files. Returns paths to remove (keeps longer version)."""
    hash_groups: dict[str, list[tuple[Path, int]]] = {}
    for path, content in files.items():
        h = content_hash(content)
        wc = word_count(content)
        hash_groups.setdefault(h, []).append((path, wc))

    to_remove = []
    for group in hash_groups.values():
        if len(group) < 2:
            continue
        # Keep the longest file, remove the rest
        group.sort(key=lambda x: x[1], reverse=True)
        for path, _ in group[1:]:
            to_remove.append(path)

    return to_remove


def process_kb(kb_dir: Path, min_words: int, dry_run: bool) -> None:
    # Only collect actual files (skip directories that end in .md)
    md_files = sorted(f for f in kb_dir.rglob("*.md") if f.is_file())
    print(f"Found {len(md_files)} markdown files in {kb_dir}")

    stats = {
        "total": len(md_files),
        "removed_short": 0,
        "removed_duplicate": 0,
        "stripped_frontmatter": 0,
        "stripped_footer": 0,
        "fixed_code_blocks": 0,
        "words_before": 0,
        "words_after": 0,
    }

    # Phase 1: Read all files, strip frontmatter + footer + fix code, count words
    processed: dict[Path, str] = {}
    short_files: list[tuple[Path, int]] = []

    for f in md_files:
        raw = f.read_text(errors="replace")
        stats["words_before"] += word_count(raw)

        # Strip frontmatter
        content = strip_frontmatter(raw)
        if content != raw:
            stats["stripped_frontmatter"] += 1

        # Strip footer noise
        cleaned = strip_footer_noise(content)
        if cleaned != content:
            stats["stripped_footer"] += 1

        # Fix collapsed code blocks
        cleaned, code_fixes = fix_collapsed_code_blocks(cleaned)
        stats["fixed_code_blocks"] += code_fixes

        # Clean excessive blank lines
        cleaned = clean_excessive_blank_lines(cleaned)

        # Strip trailing whitespace
        cleaned = cleaned.strip() + "\n"

        wc = word_count(cleaned)
        if wc < min_words:
            short_files.append((f, wc))
            stats["removed_short"] += 1
        else:
            processed[f] = cleaned

    # Phase 2: Near-duplicate detection
    duplicates = find_near_duplicates(processed)
    stats["removed_duplicate"] = len(duplicates)
    dup_set = set(duplicates)
    for d in duplicates:
        del processed[d]

    # Count final words
    for content in processed.values():
        stats["words_after"] += word_count(content)

    # ── Report ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  {'DRY RUN — no changes made' if dry_run else 'CLEANUP COMPLETE'}")
    print(f"{'=' * 60}")
    print(f"  Files scanned:          {stats['total']}")
    print(f"  Removed (short):        {stats['removed_short']} files (<{min_words} words)")
    print(f"  Removed (duplicate):    {stats['removed_duplicate']} files")
    print(f"  Frontmatter stripped:   {stats['stripped_frontmatter']} files")
    print(f"  Footer noise stripped:  {stats['stripped_footer']} files")
    print(f"  Code blocks fixed:      {stats['fixed_code_blocks']} blocks")
    print(f"  Files remaining:        {len(processed)}")
    print(f"  Words before:           {stats['words_before']:,}")
    print(f"  Words after:            {stats['words_after']:,}")
    print(f"  Words removed:          {stats['words_before'] - stats['words_after']:,}")
    print(f"{'=' * 60}")

    # Word count distribution of remaining files
    wc_buckets = {"80-199": 0, "200-499": 0, "500-999": 0, "1K-5K": 0, "5K+": 0}
    for content in processed.values():
        wc = word_count(content)
        if wc < 200:
            wc_buckets["80-199"] += 1
        elif wc < 500:
            wc_buckets["200-499"] += 1
        elif wc < 1000:
            wc_buckets["500-999"] += 1
        elif wc < 5000:
            wc_buckets["1K-5K"] += 1
        else:
            wc_buckets["5K+"] += 1

    print(f"\n  Word count distribution (remaining files):")
    for bucket, count in wc_buckets.items():
        pct = count / len(processed) * 100 if processed else 0
        bar = "█" * int(pct / 2)
        print(f"    {bucket:>8}: {count:>5} ({pct:5.1f}%) {bar}")

    if short_files:
        print(f"\n  Short files removed ({len(short_files)}):")
        short_files.sort(key=lambda x: x[1])
        for f, wc in short_files[:20]:
            print(f"    {wc:>4}w  {f.relative_to(kb_dir)}")
        if len(short_files) > 20:
            print(f"    ... and {len(short_files) - 20} more")

    if duplicates:
        print(f"\n  Duplicate files removed ({len(duplicates)}):")
        for f in duplicates[:20]:
            print(f"    {f.relative_to(kb_dir)}")
        if len(duplicates) > 20:
            print(f"    ... and {len(duplicates) - 20} more")

    if dry_run:
        print(f"\n  Run without --dry-run to apply changes.")
        return

    # ── Apply changes ───────────────────────────────────────────────────
    for f, _ in short_files:
        f.unlink()

    for f in duplicates:
        f.unlink()

    for f, content in processed.items():
        f.write_text(content)

    # Remove empty directories
    removed_dirs = 0
    for dirpath, dirnames, filenames in os.walk(kb_dir, topdown=False):
        dp = Path(dirpath)
        if dp != kb_dir and not any(dp.iterdir()):
            dp.rmdir()
            removed_dirs += 1

    if removed_dirs:
        print(f"\n  Removed {removed_dirs} empty directories")

    print("  Done.")


def main():
    parser = argparse.ArgumentParser(description="Clean up crawled KB for AI consumption")
    parser.add_argument("kb_dir", type=Path, help="Knowledge base directory")
    parser.add_argument("--min-words", type=int, default=80,
                        help="Remove files below this word count (default: 80)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without applying")
    args = parser.parse_args()

    if not args.kb_dir.is_dir():
        print(f"Error: {args.kb_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    process_kb(args.kb_dir, args.min_words, args.dry_run)


if __name__ == "__main__":
    main()
