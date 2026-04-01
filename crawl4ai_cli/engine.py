from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter, DomainFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter

from .config import SiteConfig, CrawlJobConfig

logger = logging.getLogger("crawl4ai_cli")

CACHE_MODE_MAP = {
    "bypass": CacheMode.BYPASS,
    "enabled": CacheMode.ENABLED,
    "disabled": CacheMode.DISABLED,
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid",
}

LOCALE_PATTERN = re.compile(r"^/([a-z]{2}(-[a-z]{2})?)/", re.IGNORECASE)

RETRYABLE_STATUS_CODES = {429, 503, 502, 500}


class ExcludePatternFilter:
    def __init__(self, patterns: list[str]) -> None:
        self.patterns = patterns

    def apply(self, url: str) -> bool:
        return not any(fnmatch.fnmatch(url, p) for p in self.patterns)


@dataclass
class CrawlStats:
    root_url: str = ""
    pages_crawled: int = 0
    pages_failed: int = 0
    pages_skipped: int = 0
    max_depth_reached: int = 0
    total_retries: int = 0


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {k: v for k, v in params.items() if k.lower() not in TRACKING_PARAMS}
    clean_query = urlencode(filtered, doseq=True) if filtered else ""
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, clean_query, ""))


def _strip_locale_prefix(path: str) -> str:
    return LOCALE_PATTERN.sub("/", path)


def content_hash(text: str) -> str:
    words = text.split()[:500]
    return hashlib.md5(" ".join(words).encode()).hexdigest()


def _build_filter_chain(site: SiteConfig) -> FilterChain | None:
    filters: list[Any] = []
    if site.domain_only:
        domain = urlparse(site.url).netloc
        filters.append(DomainFilter(allowed_domains=[domain]))
    if site.include_patterns:
        filters.append(URLPatternFilter(patterns=site.include_patterns))
    if not filters:
        return None
    return FilterChain(filters=filters)


def _build_run_config(site: SiteConfig, job: CrawlJobConfig) -> CrawlerRunConfig:
    filter_chain = _build_filter_chain(site)

    strategy = BFSDeepCrawlStrategy(
        max_depth=site.max_depth,
        max_pages=site.max_pages,
        include_external=not site.domain_only,
        filter_chain=filter_chain,
    )

    md_generator = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(threshold=job.pruning_threshold),
    )

    config_kwargs: dict[str, Any] = dict(
        deep_crawl_strategy=strategy,
        markdown_generator=md_generator,
        cache_mode=CACHE_MODE_MAP.get(job.cache_mode, CacheMode.BYPASS),
        stream=True,
        mean_delay=job.delay,
        max_range=job.max_range,
        semaphore_count=job.concurrency,
        wait_until=site.wait_until,
        page_timeout=site.page_timeout * 1000,
        verbose=job.verbose,
        # Boilerplate removal at HTML level
        excluded_tags=["nav", "header", "footer", "aside", "form"],
        excluded_selector=(
            "[class*='nav'], [class*='skip'], [class*='cookie'], "
            "[class*='banner'], [id*='sidebar'], [class*='sidebar']"
        ),
        remove_overlay_elements=True,
        remove_forms=True,
        exclude_social_media_links=True,
        word_count_threshold=10,
        magic=True,
    )

    if site.css_selector:
        config_kwargs["css_selector"] = site.css_selector

    if site.wait_for:
        config_kwargs["wait_for"] = f"css:{site.wait_for}"

    return CrawlerRunConfig(**config_kwargs)


OnResultCallback = Callable[[Any, SiteConfig, int, int], Awaitable[None]]


def _is_retryable(result) -> bool:
    if not result.success:
        status = getattr(result, "status_code", None)
        if status and status in RETRYABLE_STATUS_CODES:
            return True
        error = getattr(result, "error_message", "") or ""
        if any(kw in error.lower() for kw in ("timeout", "timed out", "connection")):
            return True
    return False


async def _process_results(
    results,
    site: SiteConfig,
    job: CrawlJobConfig,
    stats: CrawlStats,
    on_result: OnResultCallback,
    exclude_filter: ExcludePatternFilter | None,
    seen_urls: set[str],
    seen_hashes: set[str],
    seen_locale_paths: dict[str, str],
) -> None:
    items = []
    if hasattr(results, "__aiter__"):
        async for r in results:
            items.append(r)
    elif isinstance(results, list):
        items = results
    else:
        items = [results]

    for result in items:
        depth = result.metadata.get("depth", 0) if result.metadata else 0
        stats.max_depth_reached = max(stats.max_depth_reached, depth)
        url = getattr(result, "url", "") or ""
        norm_url = normalize_url(url)

        # URL dedup
        if norm_url in seen_urls:
            stats.pages_skipped += 1
            logger.debug("SKIP url-dedup: %s", url)
            continue
        seen_urls.add(norm_url)

        # Locale dedup — only skip if the same canonical path was seen under a DIFFERENT locale
        if site.skip_locale_duplicates:
            parsed = urlparse(norm_url)
            canonical_path = _strip_locale_prefix(parsed.path)
            if canonical_path != parsed.path:  # URL actually has a locale prefix
                locale_key = f"{parsed.netloc}{canonical_path}"
                locale_match = LOCALE_PATTERN.match(parsed.path)
                current_locale = locale_match.group(1) if locale_match else ""
                stored = seen_locale_paths.get(locale_key)
                if stored is not None and stored != current_locale:
                    stats.pages_skipped += 1
                    logger.debug("SKIP locale-dedup: %s (already have %s)", url, stored)
                    continue
                if stored is None:
                    seen_locale_paths[locale_key] = current_locale

        if not result.success:
            stats.pages_failed += 1
            error_msg = getattr(result, "error_message", "Unknown error")
            logger.warning("Failed: %s — %s", url, error_msg)
            await on_result(result, site, depth, 0)
            continue

        if exclude_filter and not exclude_filter.apply(url):
            stats.pages_skipped += 1
            logger.debug("SKIP exclude-filter: %s", url)
            continue

        # Content dedup
        if site.deduplicate_content:
            md_text = str(result.markdown) if result.markdown else ""
            if md_text:
                h = content_hash(md_text)
                if h in seen_hashes:
                    stats.pages_skipped += 1
                    logger.debug("SKIP content-dedup: %s (hash=%s)", url, h)
                    continue
                seen_hashes.add(h)
            else:
                logger.debug("SKIP empty-markdown: %s", url)
                stats.pages_skipped += 1
                continue

        stats.pages_crawled += 1
        await on_result(result, site, depth, 0)


async def crawl_site(
    crawler: AsyncWebCrawler,
    site: SiteConfig,
    job: CrawlJobConfig,
    on_result: OnResultCallback,
) -> CrawlStats:
    stats = CrawlStats(root_url=site.url)
    run_config = _build_run_config(site, job)
    exclude_filter = ExcludePatternFilter(site.exclude_patterns) if site.exclude_patterns else None
    seen_urls: set[str] = set()
    seen_hashes: set[str] = set()
    seen_locale_paths: dict[str, str] = {}

    for attempt in range(1 + job.max_retries):
        try:
            results = await crawler.arun(url=site.url, config=run_config)
            await _process_results(
                results, site, job, stats, on_result,
                exclude_filter, seen_urls, seen_hashes, seen_locale_paths,
            )
            break  # success — no retry needed
        except (TimeoutError, ConnectionError, OSError) as exc:
            stats.total_retries += 1
            if attempt < job.max_retries:
                wait = job.retry_delay * (2 ** attempt)
                logger.warning(
                    "Retryable error on %s (attempt %d/%d): %s — retrying in %.1fs",
                    site.url, attempt + 1, job.max_retries + 1, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error("Failed after %d attempts: %s — %s", attempt + 1, site.url, exc)
        except Exception:
            logger.exception("Unexpected error crawling %s", site.url)
            break

    return stats


async def run_job(
    job: CrawlJobConfig,
    on_result: OnResultCallback,
    on_site_done: Callable[[CrawlStats], Awaitable[None]] | None = None,
) -> list[CrawlStats]:
    browser_config = BrowserConfig(headless=job.headless)
    all_stats: list[CrawlStats] = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for site in job.sites:
            logger.info("Starting crawl: %s (depth=%d, max_pages=%d)", site.url, site.max_depth, site.max_pages)
            stats = await crawl_site(crawler, site, job, on_result)
            all_stats.append(stats)
            logger.info(
                "Done: %s — %d crawled, %d failed, %d skipped, %d retries",
                site.url, stats.pages_crawled, stats.pages_failed,
                stats.pages_skipped, stats.total_retries,
            )
            if on_site_done:
                await on_site_done(stats)

    return all_stats
