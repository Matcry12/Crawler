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
    re.compile(r"^\s*Prove your humanity.*$", re.IGNORECASE),
    re.compile(r"^\s*Complete the challenge below.*$", re.IGNORECASE),
    re.compile(r"^\s*!\[\]\(https://www\.google\.com/s2/favicons.*?\)\s*$"),  # favicon images
    re.compile(r"^\s*Subscribe to (?:our )?newsletter.*$", re.IGNORECASE),
    re.compile(r"^\s*Accept (?:all )?cookies?\s*$", re.IGNORECASE),
    re.compile(r"^\s*We use cookies.*$", re.IGNORECASE),
]

_GITHUB_NOISE = [
    re.compile(r"^\s*\|\s*Name\s*\|\s*Name\s*\|.*$"),  # file browser table header
    re.compile(r"^\s*\|\s*---\s*\|\s*---\s*\|"),  # table separator after file browser header
    re.compile(r"^\s*\|\s*\[.*?\]\(.*?/tree/.*?\)"),  # directory row links in file browser
    re.compile(r"^\s*\|\s*\[.*?\]\(.*?/blob/.*?\)"),  # file row links in file browser
    re.compile(r"^\s*\[!\[.*?\]\(https://avatars\.githubusercontent\.com"),  # avatar images
    re.compile(r"^\s*Go to file\s*$", re.IGNORECASE),
    re.compile(r"^\s*Open more actions menu\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s+Commits?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[\*\*\d+\*\*\s+Commits?\]\(", re.IGNORECASE),
    re.compile(r"^\s*\[\*\*\d+\*\*\s+Branch", re.IGNORECASE),
    re.compile(r"^\s*\[\*\*\d+\*\*\s+Tags?\]\(", re.IGNORECASE),
    re.compile(r"^\s*\{\{\s*message\s*\}\}\s*$"),  # GitHub template variable
    re.compile(r"^\s*\[Open commit details\]", re.IGNORECASE),
    re.compile(r"^\s*\*?\s*Public\s*$"),
    re.compile(r"^\s*\[.*?\]\(https://github\.com/.*?\)\s*/\s*\*\*\[.*?\]\(.*?\)\s*\*\*\s*Public\s*$"),  # user/repo Public header
    re.compile(r"^\s*Code\s*$"),  # standalone "Code" button
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


def _strip_html_tags(text: str) -> str:
    """Strip real HTML tags while preserving < in code blocks, comparisons, and heredocs."""
    # Split by code fences to protect code blocks
    parts = re.split(r"(```[\s\S]*?```|`[^`]+`)", text)
    for i, part in enumerate(parts):
        if part.startswith("`"):
            continue  # skip code blocks
        # Only strip known HTML tags, not arbitrary < usage
        parts[i] = re.sub(
            r"</?(?:div|span|p|br|hr|img|a|ul|ol|li|table|tr|td|th|thead|tbody"
            r"|section|article|details|summary|figcaption|figure|iframe|script"
            r"|style|link|meta|input|button|select|option|label|textarea"
            r"|small|strong|em|b|i|u|s|sup|sub|mark|abbr|cite|code|pre"
            r"|blockquote|dl|dt|dd|caption|col|colgroup|svg|path|rect|circle"
            r"|noscript|picture|source|video|audio|canvas|embed|object"
            r")(?:\s[^>]*)?\s*/?>",
            "",
            parts[i],
            flags=re.IGNORECASE,
        )
    return "".join(parts)


def _is_reddit_url(url: str) -> bool:
    return "reddit.com" in urlparse(url).netloc


# Patterns for Reddit ad/promoted blocks
_REDDIT_AD_START = re.compile(
    r"^\s*\[.*?\]\(https://www\.reddit\.com/user/.*?\)\s*•\s*\[\s*Promoted\s*\]",
    re.IGNORECASE,
)
_REDDIT_NOISE = [
    re.compile(r"^\s*Read more\s*$", re.IGNORECASE),
    re.compile(r"^\s*Share\s*$", re.IGNORECASE),
    re.compile(r"^\s*\* \* \*\s*$"),
    re.compile(r"^\s*\d+ more repl(y|ies)\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[\s*Continue this thread\s*\]\(.*?\)\s*$", re.IGNORECASE),
    re.compile(r"^\s*Learn More\s*$", re.IGNORECASE),
    re.compile(r"^\s*Play Now\s*$", re.IGNORECASE),
    re.compile(r"^\s*Sorry, something went wrong when loading this video\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*!\[Thumbnail image:.*?\]\(.*?\)\s*$"),
    re.compile(r"^\d+\s*$"),  # standalone vote counts
]

# Comment line: • [ 7h ago ](link)
_REDDIT_COMMENT_TS = re.compile(
    r"^\s*•\s*\[\s*(\d+\w?\s*ago)\s*\]\(https://www\.reddit\.com/.*/comment/.*?\)\s*$"
)

# Post header: [r/subreddit](link) • 10h ago
_REDDIT_POST_HEADER = re.compile(
    r"^\s*\[r/\w+\]\(.*?\)\s*•\s*(\d+\w?\s*ago)\s*$"
)


def _format_reddit(text: str) -> str:
    """Format Reddit crawl output: separate post from comments, strip ads."""
    lines = text.split("\n")
    out: list[str] = []
    in_ad = False
    comments_started = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect ad block start (Promoted)
        if _REDDIT_AD_START.match(stripped):
            in_ad = True
            i += 1
            continue

        # In ad block — skip until next comment timestamp or blank section
        if in_ad:
            if _REDDIT_COMMENT_TS.match(stripped) or (stripped == "" and i + 1 < len(lines) and _REDDIT_COMMENT_TS.match(lines[i + 1].strip())):
                in_ad = False
                # don't skip — fall through to process this line
            else:
                i += 1
                continue

        # Also catch promoted blocks starting with [ ](user_link) [ ](user_link)
        if re.match(r"^\s*\[\s*\]\(https://www\.reddit\.com/user/.*?\)\s*\[\s*\]\(", stripped):
            in_ad = True
            i += 1
            continue

        # Also catch "[ u/Username](link) • [ Promoted ]" variant
        if re.match(r"^\s*\[\s*u/\w+\]\(.*?\)\s*•\s*\[\s*Promoted\s*\]", stripped, re.IGNORECASE):
            in_ad = True
            i += 1
            continue

        # Strip Reddit noise lines
        if any(pat.match(stripped) for pat in _REDDIT_NOISE):
            i += 1
            continue

        # Comment timestamp → format as comment header
        m = _REDDIT_COMMENT_TS.match(stripped)
        if m:
            timestamp = m.group(1)
            if not comments_started:
                comments_started = True
                out.append("")
                out.append("---")
                out.append("## Comments")
                out.append("")
            out.append(f"---")
            out.append(f"**Comment** · {timestamp}")
            out.append("")
            i += 1
            continue

        # Post header line — clean up
        m = _REDDIT_POST_HEADER.match(stripped)
        if m:
            i += 1
            continue

        out.append(line)
        i += 1

    return "\n".join(out)


def _is_github_url(url: str) -> bool:
    return "github.com" in urlparse(url).netloc

def _format_github(text: str) -> str:
    """Strip GitHub UI chrome: file browser, avatars, commit metadata.

    Strategy: GitHub pages have a README section that starts with a markdown heading.
    Everything before the first heading is UI chrome (repo header, file browser, commit info).
    We skip all of that and keep only from the first heading onward, then clean remaining noise.
    """
    lines = text.split("\n")

    # Find the first markdown heading — that's where README content starts
    readme_start = None
    for i, line in enumerate(lines):
        if re.match(r"^#{1,6}\s+\S", line.strip()):
            readme_start = i
            break

    if readme_start is not None:
        lines = lines[readme_start:]

    # Second pass: remove any remaining GitHub noise patterns
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if any(pat.match(stripped) for pat in _GITHUB_NOISE):
            continue
        # Skip commit message artifacts: lines ending with "| date |" pattern
        if re.search(r"\|\s*[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}\s*\|\s*$", stripped):
            continue
        # Skip Co-Authored-By lines outside code blocks
        if re.match(r'^Co-Authored-By:', stripped):
            continue
        # Skip lines that are just ") |" or similar table cell remnants
        if re.match(r'^["\')]+\s*\)\s*\|', stripped):
            continue
        out.append(line)
    return "\n".join(out)


def _is_hn_url(url: str) -> bool:
    return "news.ycombinator.com" in urlparse(url).netloc

def _format_hackernews(text: str) -> str:
    """Clean up Hacker News: strip nav tables, clean comment formatting."""
    lines = text.split("\n")
    out: list[str] = []
    skip_nav = True  # skip initial nav table
    for line in lines:
        stripped = line.strip()
        # Skip the HN nav bar table at the top
        if skip_nav:
            if stripped.startswith("| **[Hacker News]") or stripped.startswith("|  "):
                continue
            if stripped.startswith("| ---"):
                continue
            if stripped and not stripped.startswith("|"):
                skip_nav = False
        # Strip table wrapper around comments — convert to plain text
        if re.match(r"^\|\s+\|$", stripped) or re.match(r"^\|\s*---\s*\|$", stripped):
            continue
        # Unwrap table cell content: "| content |" -> "content"
        m = re.match(r"^\|\s*(.*?)\s*\|?\s*$", stripped)
        if m and not stripped.startswith("| ---"):
            content = m.group(1).strip()
            if content:
                out.append(content)
            continue
        out.append(line)
    return "\n".join(out)


def clean_markdown(text: str, url: str = "") -> str:
    text = _strip_html_tags(text)
    text = _strip_boilerplate_lines(text)
    text = _strip_orphan_links(text)
    if _is_reddit_url(url):
        text = _format_reddit(text)
    if _is_github_url(url):
        text = _format_github(text)
    if _is_hn_url(url):
        text = _format_hackernews(text)
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

    content = clean_markdown(content, url=url)

    quality_issue = check_quality(content, min_word_count)
    if quality_issue:
        return None, quality_issue

    frontmatter = build_frontmatter(url, content, depth)
    full_content = frontmatter + "\n" + content + "\n"

    filepath = url_to_filepath(url, output_dir)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(full_content, encoding="utf-8")
    return filepath, "success"
