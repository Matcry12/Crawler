#!/usr/bin/env python3
"""Crawl the ~70 remaining URLs from the 882-URL collection.

Skips uncrawlable URLs (Udemy paywall, login-required, ad redirects, competitors).
Uses crawl4ai AsyncWebCrawler directly for best results.

Usage:
    source .venv/bin/activate
    python3 crawl_remaining.py --output ./claude-kb
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# ── Uncrawlable URLs (paywall, login, ads, competitors, dead) ──────────────
SKIP_URLS = {
    # Udemy — paywall/login
    "https://www.udemy.com/course/learn-claude-code/",
    "https://www.udemy.com/topic/claude-code/",
    "https://www.udemy.com/",
    "https://www.udemy.com/course/claude-ai-training-the-ultimate-course/",
    "https://www.udemy.com/course/claude-code-for-salesforce-development-mcp-agent-workflow/",
    "https://www.udemy.com/course/agentic-ai-mastery-claude-code-clawdbot-beyond/",
    "https://www.udemy.com/course/agent-skills-claude-code-cursor-and-mcp-in-practice/",
    "https://www.udemy.com/course/claude-mastery/",
    "https://www.udemy.com/course/mastering-claude-ai-build-ai-apps-agents-mcp-systems/",
    "https://www.udemy.com/course/claude-code-getting-started/",
    # ClassCentral — thin course listings
    "https://www.classcentral.com/course/udemy-claude-ai-458764",
    "https://www.classcentral.com/subject/claude",
    "https://www.classcentral.com/course/youtube-anthropic-mcp-with-ollama-no-claude-watch-this-388941",
    "https://www.classcentral.com/course/youtube-how-to-use-anthropic-s-model-context-protocol-mcp-setup-tutorial-385762",
    "https://www.classcentral.com/course/youtube-claude-code-on-the-go-2-ways-to-fix-issues-without-your-laptop-524537",
    # Login required
    "https://claude.ai/",
    "https://anthropic.skilljar.com/",
    # Bing ad redirect
    "https://www.bing.com/aclick?ld=e8os3-B-CCzeQkyKd8DTJPezVUCUzdkQRPWw-5c800uagzUKRzJcisN6w1Z0Spd3pcsObKTFOljjgMWT2EKOXpaUaG8C8b4YSmf2PJWuDFoYIyfJ2o2JfhyZ896OYk-eXAkb8MDlZcj188GCAl5gtjV2OkttttR6-hx7BVBT1YPn2eX74m0JVcaoBzu3042El0G2FukznJRgE2ammcYBdYBM7E8AU&u=aHR0cHMlM2ElMmYlMmZjaGF0LmNoYXRib3RhcHAuYWklMmZjbGF1ZGUlM2Z1dG1fc291cmNlJTNkTWljcm9zb2Z0QWRzJTI2dXRtX21lZGl1bSUzZGNwYyUyNnV0bV9jYW1wYWlnbiUzZENoYXRib3RBcHBfQmluZ19Cb3RoX1VTX3RDUEFfU2VhcmNoXzE3MDMyNiUyNnV0bV9pZCUzZDUyNDAxMTIwMiUyNnV0bV90ZXJtJTNkMTMxNjExODIwODE4MTA1OSUyNnV0bV9jb250ZW50JTNkJTI2bXNjbGtpZCUzZGRhZjNhNzU2OGFiNDFlNjk1NzRiY2U4NTVmMTIyNWFm&rlid=daf3a7568ab41e69574bce855f1225af",
    # Competitors / not Claude content
    "https://aider.chat/",
    "https://github.com/features/copilot",
    # Wikipedia generic
    "https://en.wikipedia.org/wiki/Model_Context_Protocol",
}

# Medium URL — handle separately via crawl_blocked.py
MEDIUM_URLS = {
    "https://medium.com/@Rajdip27/claude-code-vs-github-copilot-vs-cursor-i-used-all-3-for-a-month-heres-my-verdict-a647abe58080",
}


def url_to_output_path(url: str, output_dir: Path) -> Path:
    parsed = urlparse(url)
    path_part = parsed.netloc + parsed.path.rstrip("/")
    path_part = re.sub(r'[?#].*', '', path_part)
    return output_dir / (path_part + ".md")


async def crawl_urls(urls: list[str], output_dir: Path, concurrency: int = 5, delay: float = 1.5):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

    browser_config = BrowserConfig(
        headless=True,
        user_agent_mode="random",
    )

    config_kwargs = dict(
        cache_mode=CacheMode.BYPASS,
        page_timeout=90000,
        wait_until="networkidle",
        delay_before_return_html=2.0,
        scan_full_page=True,
        scroll_delay=0.3,
        remove_overlay_elements=True,
        excluded_tags=["nav", "header", "footer", "aside", "form"],
        exclude_external_links=True,
        magic=True,
        simulate_user=True,
        override_navigator=True,
    )

    total_ok = 0
    total_fail = 0
    total_words = 0
    failed_urls = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        sem = asyncio.Semaphore(concurrency)

        async def crawl_one(url: str) -> tuple[bool, int]:
            async with sem:
                try:
                    config = CrawlerRunConfig(**config_kwargs)
                    result = await crawler.arun(url=url, config=config)

                    if not result.success:
                        print(f"  FAIL [{result.status_code}] {url}")
                        return False, 0

                    content = result.markdown
                    if hasattr(result, 'markdown_v2') and result.markdown_v2:
                        if hasattr(result.markdown_v2, 'fit_markdown') and result.markdown_v2.fit_markdown:
                            content = result.markdown_v2.fit_markdown

                    if not content or len(content.strip()) < 30:
                        print(f"  FAIL [empty] {url}")
                        return False, 0

                    content = content.strip()
                    wc = len(content.split())

                    if wc < 50:
                        print(f"  SKIP [{wc}w] {url}")
                        return False, 0

                    # Write with frontmatter
                    out_path = url_to_output_path(url, output_dir)
                    out_path.parent.mkdir(parents=True, exist_ok=True)

                    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    frontmatter = f"---\nsource_url: {url}\ncrawled_at: {now}\nword_count: {wc}\n---\n\n"
                    out_path.write_text(frontmatter + content + "\n")

                    print(f"  OK   [{wc:>5}w] {url}")
                    await asyncio.sleep(delay)
                    return True, wc

                except Exception as e:
                    print(f"  ERR  [{e}] {url}")
                    return False, 0

        tasks = [crawl_one(url) for url in urls]
        results = await asyncio.gather(*tasks)

        for i, (ok, wc) in enumerate(results):
            if ok:
                total_ok += 1
                total_words += wc
            else:
                total_fail += 1
                failed_urls.append(urls[i])

    return total_ok, total_fail, total_words, failed_urls


def main():
    parser = argparse.ArgumentParser(description="Crawl remaining URLs from 882 collection")
    parser.add_argument("--output", "-o", type=Path, default=Path("./claude-kb"))
    parser.add_argument("--concurrency", "-c", type=int, default=5)
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()

    # Actual 73 missing URLs (verified via path + source_url frontmatter matching)
    ALL_MISSING = [
        "https://claudecode.io/tutorials/claude-md-setup",
        "https://habr.com/en/articles/988538/",
        "https://yyiki.org/wiki/Claude+Code/",
        "https://www.reddit.com/r/ClaudeAI/comments/1944yiy/mastering_claude_ai_a_comprehensive_guide_on_how/",
        "https://www.grammarly.com/blog/ai/what-is-claude-ai/",
        "https://www.verdent.ai/guides/how-to-use-claude-ai-for-free-2026",
        "https://smythos.com/developers/agent-integrations/create-ai-agents-using-claude/",
        "https://discuss.huggingface.co/t/10-essential-claude-code-best-practices-you-need-to-know/174731",
        "https://rosmur.github.io/claudecode-best-practices/",
        "https://www.aihero.dev/cohorts/claude-code-for-real-engineers-2026-04",
        "https://knowtechie.com/claude-code-web-interface/",
        "https://codecut.ai/claude-code-techniques-tips/",
        "https://habr.com/ru/articles/909866/",
        "https://herdora.mintlify.app/ai-tools/claude-code",
        "https://www.verdent.ai/guides/claude-skills-plugins",
        "https://forum.blocsapp.com/t/plans-for-mcp-integration-with-claude-code/27838",
        "https://kilo.ai/docs/providers/claude-code",
        "https://blog.getbind.co/2025/08/26/how-to-install-claude-code-cli/",
        "https://docs.symbolica.ai/ai/claude-code",
        "https://oboe.com/learn/mastering-claude-code-cli-for-non-developers-i1msna",
        "https://claudecode.co.com/",
        "https://www.mintlify.com/VineeTagarwaL-code/claude-code/configuration/claudemd",
        "https://dometrain.com/blog/creating-the-perfect-claudemd-for-claude-code/",
        "https://www.datacamp.com/tutorial/writing-the-best-claude-md",
        "https://habr.com/ru/articles/987094/",
        "https://www.claudeinsider.com/docs/configuration/claude-md",
        "https://hexdocs.pm/claude/guide-hooks.html",
        "https://pasqualepillitteri.it/en/news/657/claude-code-hooks-complete-guide",
        "https://www.mintlify.com/VineeTagarwaL-code/claude-code/guides/multi-agent",
        "https://aidisruption.ai/p/proper-usage-and-case-demonstrations",
        "https://creatoreconomy.so/p/full-tutorial-build-an-app-with-multiple",
        "https://adambernard.com/kb/ai/models/specific-models/claude/oh-my-claude-code-omc-agent-swarm-orchestration/",
        "https://agentskills.so/skills/yeachan-heo-oh-my-claudecode-omc-setup",
        "https://skillsmp.com/skills/yeachan-heo-oh-my-claudecode-skills-omc-setup-skill-md",
        "https://docs.claudekit.cc/docs/engineer/commands",
        "https://claudefa.st/blog/guide/mechanics/auto-memory",
        "https://www.datacamp.com/tutorial/claude-mem-guide",
        "https://joseparreogarcia.substack.com/p/claude-code-memory-explained",
        "https://www.pulsemcp.com/servers/ladislavsopko-neo-cortex-memory",
        "https://www.datacamp.com/tutorial/mcp-model-context-protocol",
        "https://obot.ai/resources/learning-center/mcp-anthropic-2/",
        "https://github.com/modu-ai/moai-adk/blob/main/src/moai_adk/templates/.claude/skills/moai-foundation-claude/reference/claude-code-headless-official.md",
        "https://www.greaterwrong.com/posts/MQGAMHQNTFyJTke2H/claude-codes",
        "https://aiproductivity.ai/blog/claude-code-prompt-engineering/",
        "https://salas.com/raindrops/26-01-14-3-Claude-Code-Everything-You-Nee.html",
        "https://claudecode.io/guides/first-steps",
        "https://dsheiko.com/weblog/pair-programming-with-claude-code/",
        "https://community.deeplearning.ai/t/new-course-enroll-in-claude-code-a-highly-agentic-coding-assistant/868227",
        "https://interworks.com/blog/channel/claude/",
        "https://letanure.hashnode.dev/building-photoroom-cli-with-claude-code-from-api-to-npm-in-3-days",
        "https://www.verdent.ai/guides/claude-code-agent-skills",
        "https://claudecode.io/guides/git-workflow",
        "https://visualstudiomagazine.com/articles/2025/05/05/two-different-takes-on-cursor-copilot-vibe-coding-supremacy.aspx",
        "https://forum.cursor.com/t/cursor-vs-claude-code-looking-for-community-feedback/148153",
    ]

    # Filter out skips and medium
    crawl_list = [u for u in ALL_MISSING if u not in SKIP_URLS and u not in MEDIUM_URLS]

    # Build source_url index from existing files to catch different-path matches
    existing_source_urls = set()
    for p in args.output.rglob("*.md"):
        if p.is_file():
            try:
                head = p.read_text()[:500]
                m = re.search(r'source_url:\s*(\S+)', head)
                if m:
                    existing_source_urls.add(m.group(1).rstrip("/"))
            except:
                pass

    # Skip if already exists (by path OR by source_url frontmatter)
    final = []
    for url in crawl_list:
        url_clean = url.rstrip("/")
        out_path = url_to_output_path(url, args.output)
        if out_path.exists() and len(out_path.read_text().split()) >= 50:
            print(f"  SKIP [exists] {url}")
            continue
        if url_clean in existing_source_urls:
            print(f"  SKIP [source_url match] {url}")
            continue
        final.append(url)

    print(f"\n{'='*60}")
    print(f"  Crawling {len(final)} remaining URLs")
    print(f"  Skipped: {len(SKIP_URLS)} uncrawlable, {len(MEDIUM_URLS)} medium (use crawl_blocked.py)")
    print(f"{'='*60}\n")

    if not final:
        print("Nothing to crawl!")
        return

    ok, fail, words, failed = asyncio.run(crawl_urls(final, args.output, args.concurrency, args.delay))

    print(f"\n{'='*60}")
    print(f"  DONE: {ok} OK, {fail} failed, {words:,} words added")
    print(f"{'='*60}")

    if failed:
        print(f"\nFailed URLs ({len(failed)}):")
        for u in failed:
            print(f"  {u}")


if __name__ == "__main__":
    main()
