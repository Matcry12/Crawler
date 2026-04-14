#!/usr/bin/env python3
"""Crawl blocked URLs: Medium via Freedium, PDFs via download+convert.

Usage:
    python3 crawl_blocked.py --output ./claude-kb --delay 3.0
    python3 crawl_blocked.py --output ./claude-kb --only medium
    python3 crawl_blocked.py --output ./claude-kb --only pdf
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

FREEDIUM_MIRROR = "https://freedium-mirror.cfd"

MEDIUM_URLS = [
    "https://alirezarezvani.medium.com/how-to-build-claude-code-agents-from-scratch-the-10-step-framework-i-actually-use-in-production-6f6a358f4f8c",
    "https://annaarteeva.medium.com/the-vibe-coders-prompting-guide-e04ba0295a18",
    "https://bytebridge.medium.com/model-context-protocol-mcp-and-the-mcp-gateway-concepts-architecture-and-case-studies-3470b6d549a1",
    "https://darasoba.medium.com/how-to-set-up-and-use-claude-code-agent-teams-and-actually-get-great-results-9a34f8648f6d",
    "https://dbxdev.medium.com/turning-databricks-into-an-ai-pair-programmer-with-claude-powered-coding-agents-1665ad0bb43f",
    "https://dinanjana.medium.com/mastering-the-vibe-claude-code-best-practices-that-actually-work-823371daf64c",
    "https://dinukasal.medium.com/cursor-or-claude-code-or-copilot-759d36a0dff4",
    "https://generativeai.pub/after-a-week-of-claude-code-10-things-i-wish-i-knew-on-day-one-81fd2a542c67",
    "https://medium.com/@Rajdip27/claude-code-vs-github-copilot-vs-cursor-i-used-all-3-for-a-month-heres-my-verdict-a647abe58080",
    "https://medium.com/@amanatulla1606/anthropics-model-context-protocol-mcp-a-deep-dive-for-developers-1d3db39c9fdc",
    "https://medium.com/@balogunkehinde3/what-i-learned-after-taking-a-full-claude-code-course-with-real-examples-e86ca0eea1cd",
    "https://medium.com/@coding_with_tech/our-ai-pair-programming-experiment-50-faster-development-200-more-bugs-100-team-burnout-468f78cc426c",
    "https://medium.com/@creativeaininja/complete-beginners-guide-to-claude-code-from-setup-to-your-first-ai-coding-session-57f43119ec62",
    "https://medium.com/@dan.avila7/claude-code-learning-path-a-practical-guide-to-getting-started-fcc601550476",
    "https://medium.com/@elisowski/mcp-explained-the-new-standard-connecting-ai-to-everything-79c5a1c98288",
    "https://medium.com/@fra.bernhardt/automate-your-documentation-with-claude-code-github-actions-a-step-by-step-guide-2be2d315ed45",
    "https://medium.com/@joe.njenga/5-new-claude-code-slash-commands-that-are-making-workflows-better-7bd416a5859a",
    "https://medium.com/@joe.njenga/how-im-using-claude-code-sub-agents-newest-feature-as-my-coding-army-9598e30c1318",
    "https://medium.com/@luongnv89/claude-code-memory-teaching-claude-your-projects-dna-45c4beca6121",
    "https://medium.com/@luongnv89/discovering-claude-code-slash-commands-cdc17f0dfb29",
    "https://medium.com/@ooi_yee_fei/building-with-ai-my-still-evolving-workflow-with-claude-code-a8b5bc510877",
    "https://medium.com/@researchgraph/building-a-simple-internet-search-app-using-anthropic-model-context-protocol-eefbab2747d3",
    "https://medium.com/@richardhightower/claude-code-subagents-and-main-agent-coordination-a-complete-guide-to-ai-agent-delegation-patterns-a4f88ae8f46c",
    "https://medium.com/@salwan.mohamed/advanced-claude-code-techniques-multi-agent-workflows-and-parallel-development-for-devops-89377460252c",
    "https://medium.com/@techofhp/claude-code-and-subagents-how-to-build-your-first-multi-agent-workflow-3cdbc5e430fa",
    "https://medium.com/@tl_99311/claude-codes-memory-working-with-ai-in-large-codebases-a948f66c2d7e",
    "https://medium.com/@yeshakaniyawala/claude-code-hooks-intercept-control-automate-77e07bcde726",
    "https://medium.com/ai-ml-human-training-coaching/claude-code-power-user-slash-commands-the-complete-primer-e6ff143b3913",
    "https://medium.com/aicloudfaqs/claude-prompt-engineering-for-code-generation-best-practices-413b82a18f3a",
    "https://medium.com/aimonks/claude-code-tutorial-80037240aaab",
    "https://medium.com/data-science-collective/claude-code-memory-management-the-complete-guide-2026-b0df6300c4e8",
    "https://medium.com/data-science-collective/the-complete-guide-to-ai-agent-memory-files-claude-md-agents-md-and-beyond-49ea0df5c5a9",
    "https://medium.com/data-science-in-your-pocket/claude-code-free-course-for-beginners-by-anthropic-3e9d28520f53",
    "https://medium.com/javarevisited/i-tried-20-claude-code-courses-on-udemy-here-are-my-top-7-recommendations-for-2026-5aec9c45c85f",
    "https://medium.com/the-model-observer/building-an-agentic-rag-with-lancedb-mcp-bedrock-and-ollama-in-google-colab-8b6d4643f3f9",
    "https://naqeebali-shamsi.medium.com/the-complete-guide-to-setting-global-instructions-for-claude-code-cli-cec8407c99a0",
    "https://reading.sh/the-claude-code-setup-that-won-a-hackathon-a75a161cd41c",
    "https://uxplanet.org/7-advanced-claude-code-slash-commands-db4c9be3e38c",
    "https://webdesignseattle.medium.com/what-is-claude-25f445c47f52",
]

PDF_URLS = [
    "https://firstprinciplescg.com/wp-content/uploads/2025/09/Claude-Code-Slash-Commands-The-Complete-Reference-Guide.pdf",
    "https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf",
    "https://www-cdn.anthropic.com/58284b19e702b49db9302d5b6f135ad8871e7658.pdf",
    "https://www.belmont.edu/data/_files/claude-a-step-by-step-beginners-guide.pdf",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def url_to_output_path(url: str, output_dir: Path) -> Path:
    """Convert URL to filesystem path matching crawl4ai-cli convention."""
    parsed = urlparse(url)
    path_part = parsed.netloc + parsed.path.rstrip("/")
    # Clean query params
    path_part = re.sub(r'[?#].*', '', path_part)
    return output_dir / (path_part + ".md")


def crawl_medium_via_freedium(url: str, output_dir: Path, delay: float) -> tuple[bool, int]:
    """Crawl a Medium article via Freedium mirror."""
    freedium_url = f"{FREEDIUM_MIRROR}/{url}"
    out_path = url_to_output_path(url, output_dir)

    try:
        resp = requests.get(freedium_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            print(f"  FAIL [{resp.status_code}] {url}")
            return False, 0

        soup = BeautifulSoup(resp.text, "html.parser")

        # Freedium wraps article in main content area
        # Try several selectors
        article = (
            soup.find("article")
            or soup.find("div", class_="main-content")
            or soup.find("div", class_="post-content")
            or soup.find("main")
        )

        if not article:
            # Fallback: take the whole body but strip nav/header/footer
            article = soup.find("body")
            if article:
                for tag in article.find_all(["nav", "header", "footer", "aside", "script", "style"]):
                    tag.decompose()

        if not article:
            print(f"  FAIL [no content] {url}")
            return False, 0

        # Convert to markdown
        content = md(str(article), heading_style="ATX", code_language="python")

        # Clean up
        content = re.sub(r'\n{4,}', '\n\n\n', content)
        content = content.strip()

        wc = len(content.split())
        if wc < 50:
            print(f"  SKIP [{wc}w too short] {url}")
            return False, 0

        # Write
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content + "\n")

        print(f"  OK   [{wc:>5}w] {url}")
        return True, wc

    except Exception as e:
        print(f"  ERR  [{e}] {url}")
        return False, 0


def crawl_pdf(url: str, output_dir: Path) -> tuple[bool, int]:
    """Download PDF and convert to markdown."""
    import pymupdf4llm
    import tempfile

    out_path = url_to_output_path(url, output_dir)

    try:
        # Download PDF to temp file
        resp = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        if resp.status_code != 200:
            print(f"  FAIL [{resp.status_code}] {url}")
            return False, 0

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            for chunk in resp.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp_path = tmp.name

        # Convert to markdown
        content = pymupdf4llm.to_markdown(tmp_path)

        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)

        # Clean up
        content = re.sub(r'\n{4,}', '\n\n\n', content)
        content = content.strip()

        wc = len(content.split())
        if wc < 50:
            print(f"  SKIP [{wc}w too short] {url}")
            return False, 0

        # Write
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content + "\n")

        print(f"  OK   [{wc:>5}w] {url}")
        return True, wc

    except Exception as e:
        print(f"  ERR  [{e}] {url}")
        return False, 0


def main():
    parser = argparse.ArgumentParser(description="Crawl blocked URLs (Medium via Freedium, PDFs)")
    parser.add_argument("--output", "-o", type=Path, default=Path("./claude-kb"),
                        help="Output directory (default: ./claude-kb)")
    parser.add_argument("--delay", type=float, default=3.0,
                        help="Delay between Medium requests (default: 3.0s)")
    parser.add_argument("--only", choices=["medium", "pdf"],
                        help="Only crawl medium or pdf URLs")
    args = parser.parse_args()

    total_ok = 0
    total_fail = 0
    total_words = 0

    # Medium via Freedium
    if args.only in (None, "medium"):
        print(f"\n{'='*60}")
        print(f"  MEDIUM via Freedium ({len(MEDIUM_URLS)} articles)")
        print(f"{'='*60}\n")
        for i, url in enumerate(MEDIUM_URLS):
            # Skip if already exists
            out_path = url_to_output_path(url, args.output)
            if out_path.exists() and len(out_path.read_text().split()) >= 80:
                print(f"  SKIP [exists] {url}")
                continue

            ok, wc = crawl_medium_via_freedium(url, args.output, args.delay)
            if ok:
                total_ok += 1
                total_words += wc
            else:
                total_fail += 1

            if i < len(MEDIUM_URLS) - 1:
                time.sleep(args.delay)

    # PDFs
    if args.only in (None, "pdf"):
        print(f"\n{'='*60}")
        print(f"  PDF download + convert ({len(PDF_URLS)} files)")
        print(f"{'='*60}\n")
        for url in PDF_URLS:
            out_path = url_to_output_path(url, args.output)
            if out_path.exists() and len(out_path.read_text().split()) >= 80:
                print(f"  SKIP [exists] {url}")
                continue

            ok, wc = crawl_pdf(url, args.output)
            if ok:
                total_ok += 1
                total_words += wc
            else:
                total_fail += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"  DONE: {total_ok} OK, {total_fail} failed, {total_words:,} words added")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
