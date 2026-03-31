from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*\[\s*Skip to .*?\]\(.*?\)\s*$", re.IGNORECASE),
    re.compile(r"^\s*©\s*\d{4}", re.IGNORECASE),
    re.compile(r"^\s*All rights reserved\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[Reddit, Inc\..*?\]\(.*?\)\s*$", re.IGNORECASE),
    re.compile(r"^\s*Open sort options\s*$", re.IGNORECASE),
    re.compile(r"^\s*Change post view\s*$", re.IGNORECASE),
    re.compile(r"^\s*Install completion.*$", re.IGNORECASE),
    re.compile(r"^\s*Show completion.*$", re.IGNORECASE),
]

LOW_QUALITY_INDICATORS = [
    "loading...",
    "sign in",
    "log in to",
    "access denied",
    "403 forbidden",
    "please enable javascript",
    "this page requires javascript",
]


def url_to_filepath(url: str, output_dir: str) -> Path:
    parsed = urlparse(url)
    domain = parsed.netloc
    path = parsed.path.strip("/")
    if not path or path.endswith("/"):
        path = path.rstrip("/") + "/index" if path.rstrip("/") else "index"
    path = re.sub(r"\.(html?|php|aspx?)$", "", path)
    path = re.sub(r'[<>:"|?*]', "_", path)
    # Truncate overly long path segments
    parts = path.split("/")
    parts = [p[:200] for p in parts]
    path = "/".join(parts)
    return Path(output_dir) / domain / f"{path}.md"


def _extract_title(markdown: str) -> str:
    for line in markdown.split("\n", 50):
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def build_frontmatter(url: str, markdown: str, depth: int = 0) -> str:
    title = _extract_title(markdown)
    word_count = len(markdown.split())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "---",
        f"source_url: {url}",
    ]
    if title:
        safe_title = title.replace('"', '\\"')
        lines.append(f'title: "{safe_title}"')
    lines += [
        f"crawl_depth: {depth}",
        f"crawled_at: {now}",
        f"word_count: {word_count}",
        "---",
        "",
    ]
    return "\n".join(lines)


def _strip_boilerplate_lines(text: str) -> str:
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        if any(pat.match(line) for pat in BOILERPLATE_PATTERNS):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def _strip_orphan_links(text: str) -> str:
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just a single markdown link with no other text
        if re.match(r"^\*?\s*\[[^\]]*\]\([^)]*\)\s*\*?$", stripped) and len(stripped) < 200:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def clean_markdown(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = _strip_boilerplate_lines(text)
    text = _strip_orphan_links(text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def check_quality(content: str, min_word_count: int = 20) -> str | None:
    word_count = len(content.split())
    if word_count < min_word_count:
        return "low_quality"
    content_lower = content.lower().strip()
    for indicator in LOW_QUALITY_INDICATORS:
        if content_lower == indicator or (word_count < 30 and indicator in content_lower):
            return "low_quality"
    return None


def get_markdown_content(result, format: str = "fit") -> str:
    md = result.markdown
    if format == "citations":
        content = getattr(md, "markdown_with_citations", None) or str(md)
    elif format == "raw":
        content = getattr(md, "raw_markdown", None) or str(md)
    else:
        content = getattr(md, "fit_markdown", None) or str(md)
    return content if content else ""


def write_markdown(
    url: str,
    result,
    output_dir: str,
    format: str = "fit",
    depth: int = 0,
    min_word_count: int = 20,
) -> tuple[Path | None, str]:
    """Write markdown to disk. Returns (filepath, status)."""
    content = get_markdown_content(result, format)
    if not content or not content.strip():
        return None, "empty_content"

    content = clean_markdown(content)

    quality_issue = check_quality(content, min_word_count)
    if quality_issue:
        return None, quality_issue

    frontmatter = build_frontmatter(url, content, depth)
    full_content = frontmatter + "\n" + content + "\n"

    filepath = url_to_filepath(url, output_dir)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(full_content, encoding="utf-8")
    return filepath, "success"
