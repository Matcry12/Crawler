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
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, BestFirstCrawlingStrategy
from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter, DomainFilter, ContentRelevanceFilter
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.content_filter_strategy import PruningContentFilter, BM25ContentFilter

from .config import SiteConfig, CrawlJobConfig, UrlListConfig
from .classifier import classify_url, get_profile, get_platform_selector, get_platform_config, DomainProfile

logger = logging.getLogger("crawl4ai_cli")

CACHE_MODE_MAP = {
    "bypass": CacheMode.BYPASS,
    "enabled": CacheMode.ENABLED,
    "disabled": CacheMode.DISABLED,
    "read_only": CacheMode.READ_ONLY,
    "write_only": CacheMode.WRITE_ONLY,
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


def _build_filter_chain(site: SiteConfig, job: CrawlJobConfig) -> FilterChain | None:
    filters: list[Any] = []
    # Fast sync filters first
    if job.blocked_domains:
        filters.append(DomainFilter(blocked_domains=job.blocked_domains))
    if site.domain_only:
        domain = urlparse(site.url).netloc
        filters.append(DomainFilter(allowed_domains=[domain]))
    if site.include_patterns:
        filters.append(URLPatternFilter(patterns=site.include_patterns))
    # Slow async filters last
    if site.content_relevance_threshold > 0 and site.query:
        filters.append(ContentRelevanceFilter(
            query=site.query,
            threshold=site.content_relevance_threshold,
        ))
    if not filters:
        return None
    return FilterChain(filters=filters)


def _build_url_scorer(site: SiteConfig) -> KeywordRelevanceScorer | None:
    keywords = site.score_keywords
    if not keywords and site.query:
        keywords = site.query.lower().split()
    if not keywords:
        return None
    return KeywordRelevanceScorer(keywords=keywords, weight=1.0)


def _build_run_config(site: SiteConfig, job: CrawlJobConfig) -> CrawlerRunConfig:
    filter_chain = _build_filter_chain(site, job)

    # Content filter: adaptive thresholds from domain profile when auto-tuned
    if site.auto_tune and site.domain_type:
        from .classifier import DOMAIN_PROFILES
        profile = DOMAIN_PROFILES.get(site.domain_type) or get_profile(site.url)
        if profile.content_filter == "bm25" and site.query:
            content_filter = BM25ContentFilter(
                user_query=site.query,
                bm25_threshold=profile.bm25_threshold,
            )
        else:
            content_filter = PruningContentFilter(threshold=profile.pruning_threshold)
    elif job.content_filter == "bm25" and site.query:
        content_filter = BM25ContentFilter(
            user_query=site.query,
            bm25_threshold=1.0,
        )
    else:
        content_filter = PruningContentFilter(threshold=job.pruning_threshold)

    md_generator = DefaultMarkdownGenerator(content_filter=content_filter)

    config_kwargs: dict[str, Any] = dict(
        markdown_generator=md_generator,
        cache_mode=CACHE_MODE_MAP.get(job.cache_mode, CacheMode.BYPASS),
        check_cache_freshness=True,
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
        remove_consent_popups=True,
        process_iframes=True,
        remove_forms=True,
        exclude_social_media_links=True,
        word_count_threshold=10,
        scan_full_page=True,
        scroll_delay=0.3,
        delay_before_return_html=0.5,
        max_scroll_steps=20,
        magic=True,
    )

    # Only use deep crawl strategy when depth > 0
    if site.max_depth > 0:
        url_scorer = _build_url_scorer(site)
        strategy_kwargs: dict[str, Any] = dict(
            max_depth=site.max_depth,
            max_pages=site.max_pages,
            include_external=not site.domain_only,
        )
        if filter_chain:
            strategy_kwargs["filter_chain"] = filter_chain
        if url_scorer:
            strategy_kwargs["url_scorer"] = url_scorer
        if site.score_threshold > 0:
            strategy_kwargs["score_threshold"] = site.score_threshold

        if site.crawl_strategy == "best_first":
            strategy = BestFirstCrawlingStrategy(**strategy_kwargs)
        else:
            strategy = BFSDeepCrawlStrategy(**strategy_kwargs)

        config_kwargs["deep_crawl_strategy"] = strategy
        config_kwargs["stream"] = True

    if site.css_selector:
        config_kwargs["css_selector"] = site.css_selector

    if site.wait_for:
        config_kwargs["wait_for"] = f"css:{site.wait_for}"

    # Platform-specific JS interactions (click "Load More", expand comments, etc.)
    platform = get_platform_config(site.url)
    if platform.js_code:
        config_kwargs["js_code"] = platform.js_code
    if platform.wait_for and "wait_for" not in config_kwargs:
        config_kwargs["wait_for"] = platform.wait_for

    if job.stealth:
        config_kwargs["simulate_user"] = True
        config_kwargs["override_navigator"] = True

    return CrawlerRunConfig(**config_kwargs)


def apply_domain_profile(site: SiteConfig, profile: DomainProfile, query: str = "") -> SiteConfig:
    """Apply a DomainProfile's settings to a SiteConfig, returning a new copy."""
    overrides: dict[str, Any] = {
        "max_depth": profile.max_depth,
        "max_pages": profile.max_pages,
        "crawl_strategy": profile.crawl_strategy,
        "score_threshold": profile.score_threshold,
        "domain_only": profile.domain_only,
        "page_timeout": profile.page_timeout,
        "wait_until": profile.wait_until,
        "domain_type": profile.domain_type,
        "auto_tune": True,
    }
    if query:
        overrides["query"] = query
    # CSS selector: prefer platform-specific (e.g. Substack, Medium), then domain type profile
    css = get_platform_selector(site.url) or profile.css_selector
    if css:
        overrides["css_selector"] = css
    return site.model_copy(update=overrides)


def build_url_list_job(
    urls: list[dict],
    config: UrlListConfig,
    completed_urls: set[str] | None = None,
) -> CrawlJobConfig:
    """Build a CrawlJobConfig from a list of URL entries with auto-classification."""
    sites: list[SiteConfig] = []
    completed = completed_urls or set()
    seen: set[str] = set()

    for entry in urls:
        url = entry.get("url", "")
        if not url:
            continue
        norm = normalize_url(url)
        if norm in seen:
            continue
        seen.add(norm)
        if config.resume and norm in completed:
            continue

        topic = entry.get("topic", "") or config.global_query
        query = topic if topic else config.global_query

        site = SiteConfig(
            url=url,
            query=query,
            score_keywords=query.lower().split() if query else [],
            domain_only=True,
            skip_locale_duplicates=True,
            deduplicate_content=True,
        )

        if config.auto_classify:
            profile = get_profile(url)
            site = apply_domain_profile(site, profile, query=query)

        # Apply global overrides if set
        if config.global_depth is not None:
            site = site.model_copy(update={"max_depth": config.global_depth})
        if config.global_max_pages is not None:
            site = site.model_copy(update={"max_pages": config.global_max_pages})

        sites.append(site)

    # Determine content filter: use bm25 if any site has a query
    content_filter = "pruning"
    if any(s.query for s in sites):
        content_filter = "bm25"

    return CrawlJobConfig(
        sites=sites,
        output_dir=config.output_dir,
        delay=config.delay,
        max_range=config.max_range,
        concurrency=config.concurrency,
        headless=config.headless,
        content_filter=content_filter,
        markdown_format=config.markdown_format,
        min_word_count=config.min_word_count,
        max_retries=config.max_retries,
        retry_delay=config.retry_delay,
        stealth=config.stealth,
        verbose=config.verbose,
        blocked_domains=config.blocked_domains,
        generate_manifest=True,
        cache_mode=config.cache_mode,
    )


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


async def _run_batch_sites(
    crawler: AsyncWebCrawler,
    sites: list[SiteConfig],
    job: CrawlJobConfig,
    on_result: OnResultCallback,
    on_site_done: Callable[[CrawlStats], Awaitable[None]] | None = None,
) -> list[CrawlStats]:
    """Crawl depth=0 sites in parallel using arun_many with MemoryAdaptiveDispatcher."""
    from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher, RateLimiter

    if not sites:
        return []

    urls = [s.url for s in sites]
    configs = [_build_run_config(s, job) for s in sites]

    # Build lookup maps: exact URL, normalized URL, and positional index
    site_by_url: dict[str, SiteConfig] = {s.url: s for s in sites}
    site_by_norm: dict[str, SiteConfig] = {normalize_url(s.url): s for s in sites}

    dispatcher = MemoryAdaptiveDispatcher(
        max_session_permit=min(job.concurrency, len(sites)),
        memory_threshold_percent=85.0,
        rate_limiter=RateLimiter(
            base_delay=(job.delay, job.delay + job.max_range),
            max_retries=job.max_retries,
        ),
    )

    all_stats: list[CrawlStats] = []
    url_stats: dict[str, CrawlStats] = {
        s.url: CrawlStats(root_url=s.url) for s in sites
    }
    matched_sites: set[str] = set()

    results = await crawler.arun_many(urls=urls, config=configs, dispatcher=dispatcher)

    if not isinstance(results, list):
        results = [results]

    def _match_site(result_url: str, idx: int) -> SiteConfig | None:
        """Match a result URL back to its SiteConfig."""
        # 1. Exact URL match
        site = site_by_url.get(result_url)
        if site:
            return site
        # 2. Normalized URL match
        site = site_by_norm.get(normalize_url(result_url))
        if site:
            return site
        # 3. Domain match — find the site whose domain matches
        from urllib.parse import urlparse
        result_domain = urlparse(result_url).netloc.lower()
        for s in sites:
            if urlparse(s.url).netloc.lower() == result_domain and s.url not in matched_sites:
                return s
        # 4. Positional fallback — results often come back in order
        if 0 <= idx < len(sites) and sites[idx].url not in matched_sites:
            return sites[idx]
        return None

    for idx, result in enumerate(results):
        url = getattr(result, "url", "") or ""
        site = _match_site(url, idx)

        if not site:
            logger.warning("Could not match result URL to any site: %s", url)
            continue

        matched_sites.add(site.url)
        stats = url_stats[site.url]

        if not result.success:
            stats.pages_failed += 1
            await on_result(result, site, 0, 0)
        else:
            stats.pages_crawled += 1
            await on_result(result, site, 0, 0)

    # Report stats for each site
    for site in sites:
        stats = url_stats[site.url]
        all_stats.append(stats)
        if site.url not in matched_sites:
            logger.warning("No result received for: %s", site.url)
        else:
            logger.info(
                "Done: %s — %d crawled, %d failed",
                site.url, stats.pages_crawled, stats.pages_failed,
            )
        if on_site_done:
            await on_site_done(stats)

    return all_stats


async def run_job(
    job: CrawlJobConfig,
    on_result: OnResultCallback,
    on_site_done: Callable[[CrawlStats], Awaitable[None]] | None = None,
) -> list[CrawlStats]:
    browser_kwargs: dict[str, Any] = dict(headless=job.headless)
    if job.stealth:
        browser_kwargs["user_agent_mode"] = "random"
        browser_kwargs["enable_stealth"] = True
    browser_config = BrowserConfig(**browser_kwargs)
    all_stats: list[CrawlStats] = []

    # Split sites: depth=0 can use arun_many (parallel), depth>0 needs sequential arun
    shallow_sites = [s for s in job.sites if s.max_depth == 0]
    deep_sites = [s for s in job.sites if s.max_depth > 0]

    async with AsyncWebCrawler(config=browser_config) as crawler:
        # Phase 1: Batch-crawl all depth=0 sites in parallel
        if shallow_sites:
            logger.info("Batch crawling %d shallow sites (depth=0) via arun_many", len(shallow_sites))
            batch_stats = await _run_batch_sites(
                crawler, shallow_sites, job, on_result, on_site_done,
            )
            all_stats.extend(batch_stats)

        # Phase 2: Sequential crawl for depth>0 sites (need deep crawl strategy)
        for site in deep_sites:
            logger.info("Deep crawling: %s (depth=%d, max_pages=%d)", site.url, site.max_depth, site.max_pages)
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
