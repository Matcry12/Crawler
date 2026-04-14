from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, MofNCompleteColumn

from .engine import normalize_url

logger = logging.getLogger("crawl4ai_cli")

CONTENT_SUFFIXES = [
    "tutorial",
    "guide",
    "course",
    "examples",
    "documentation",
    "best practices",
    "tips",
    "how to",
    "beginner",
    "advanced",
]

SITE_OPERATORS = [
    "site:github.com",
    "site:medium.com",
    "site:dev.to",
    "site:youtube.com",
]

DEFAULT_EXCLUDE_DOMAINS = [
    "pinterest.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
]


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    source_query: str


def generate_query_variants(topic: str, count: int = 8) -> list[str]:
    """Generate diverse search query variants from a topic."""
    variants: list[str] = [topic]

    # Topic + content suffixes
    for suffix in CONTENT_SUFFIXES:
        variants.append(f"{topic} {suffix}")
        if len(variants) >= count + len(SITE_OPERATORS) + 1:
            break

    # Site-specific searches
    for site_op in SITE_OPERATORS:
        variants.append(f'{site_op} "{topic}"')

    # Intitle variant
    variants.append(f'intitle:"{topic}"')

    # Deduplicate and truncate
    seen: set[str] = set()
    unique: list[str] = []
    for v in variants:
        if v.lower() not in seen:
            seen.add(v.lower())
            unique.append(v)
    return unique[:count]


def search_urls(
    topic: str,
    queries_count: int = 8,
    results_per_query: int = 15,
    exclude_domains: list[str] | None = None,
    query_variants: list[str] | None = None,
    console: Console | None = None,
) -> list[SearchResult]:
    """Search DuckDuckGo with multiple query variants, return deduplicated URLs."""
    from ddgs import DDGS
    from ddgs.exceptions import RatelimitException

    if exclude_domains is None:
        exclude_domains = DEFAULT_EXCLUDE_DOMAINS

    queries = query_variants or generate_query_variants(topic, queries_count)
    ddgs = DDGS()
    seen_urls: set[str] = set()
    results: list[SearchResult] = []
    total_raw = 0

    con = console or Console()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        MofNCompleteColumn(),
        console=con,
    ) as progress:
        task = progress.add_task(f"Searching: {topic}", total=len(queries))

        for i, query in enumerate(queries):
            batch: list[dict] = []
            for attempt in range(4):
                try:
                    batch = ddgs.text(
                        query=query,
                        max_results=results_per_query,
                        safesearch="off",
                    )
                    break
                except RatelimitException:
                    wait = (3.0 * (2 ** attempt)) + random.uniform(1, 3)
                    logger.warning("Rate limited on query %d. Waiting %.1fs...", i + 1, wait)
                    time.sleep(wait)
                except Exception as e:
                    logger.warning("Search error on query '%s': %s", query, e)
                    break

            query_count = 0
            for item in batch:
                url = item.get("href", "")
                if not url:
                    continue
                total_raw += 1

                # Domain exclusion
                domain = urlparse(url).netloc.lower()
                if any(ex in domain for ex in exclude_domains):
                    continue

                norm = normalize_url(url)
                if norm in seen_urls:
                    continue
                seen_urls.add(norm)
                query_count += 1

                results.append(SearchResult(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("body", ""),
                    source_query=query,
                ))

            progress.update(task, advance=1)
            logger.info("Query %d/%d: %s → %d new URLs", i + 1, len(queries), query, query_count)

            # Delay between queries
            if i < len(queries) - 1:
                time.sleep(random.uniform(3, 6))

    dupes = total_raw - len(results)
    con.print(
        f"\n[bold green]Found {len(results)} unique URLs[/bold green] "
        f"from {len(queries)} queries ({total_raw} total, {dupes} duplicates removed)"
    )
    return results


def build_search_job(
    results: list[SearchResult],
    output_dir: str = "./output",
    crawl_depth: int = 0,
    max_pages: int = 100,
    delay: float = 1.5,
    concurrency: int = 3,
    pruning_threshold: float = 0.30,
    markdown_format: str = "fit",
    min_word_count: int = 30,
    css_selector: str | None = None,
    stealth: bool = True,
    verbose: bool = False,
) -> "CrawlJobConfig":
    """Convert search results into a CrawlJobConfig for the existing crawl pipeline."""
    from .config import SiteConfig, CrawlJobConfig

    # Cap results to max_pages
    capped = results[:max_pages]

    sites = []
    for r in capped:
        site = SiteConfig(
            url=r.url,
            max_depth=crawl_depth,
            max_pages=20 if crawl_depth > 0 else 1,
            domain_only=False,  # critical: search results span many domains
            css_selector=css_selector,
            wait_until="domcontentloaded",  # diverse sites — networkidle too risky
            page_timeout=30,
            skip_locale_duplicates=True,
            deduplicate_content=True,
        )
        sites.append(site)

    return CrawlJobConfig(
        sites=sites,
        output_dir=output_dir,
        delay=delay,
        concurrency=concurrency,
        pruning_threshold=pruning_threshold,
        markdown_format=markdown_format,  # type: ignore[arg-type]
        min_word_count=min_word_count,
        stealth=stealth,
        verbose=verbose,
        generate_manifest=True,
    )


def save_search_metadata(
    results: list[SearchResult],
    topic: str,
    queries: list[str],
    output_dir: str,
) -> Path:
    """Save search metadata alongside crawl output."""
    metadata = {
        "topic": topic,
        "searched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queries_used": queries,
        "total_urls_found": len(results),
        "urls": [
            {
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet,
                "found_by_query": r.source_query,
            }
            for r in results
        ],
    }
    path = Path(output_dir) / "search_metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
