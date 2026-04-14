from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class SiteConfig(BaseModel):
    url: str
    max_depth: int = 3
    max_pages: int = 100
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    domain_only: bool = True
    css_selector: str | None = None
    wait_until: str = "networkidle"
    wait_for: str | None = None
    page_timeout: int = 60
    skip_locale_duplicates: bool = True
    deduplicate_content: bool = True
    # URL scoring & filtering
    query: str = ""
    crawl_strategy: Literal["bfs", "best_first"] = "bfs"
    score_keywords: list[str] = Field(default_factory=list)
    score_threshold: float = 0.0
    content_relevance_threshold: float = 0.0
    # Auto-classification
    domain_type: str = ""  # auto-detected if empty: docs, github, reddit, blog, etc.
    auto_tune: bool = False  # apply DomainProfile settings automatically


class CrawlJobConfig(BaseModel):
    sites: list[SiteConfig]
    output_dir: str = "./output"
    delay: float = 1.0
    max_range: float = 0.5
    concurrency: int = 5
    headless: bool = True
    cache_mode: Literal["bypass", "enabled", "disabled", "read_only", "write_only"] = "enabled"
    pruning_threshold: float = 0.48
    content_filter: Literal["pruning", "bm25"] = "pruning"
    markdown_format: Literal["fit", "raw", "citations"] = "fit"
    generate_manifest: bool = True
    verbose: bool = False
    max_retries: int = 2
    retry_delay: float = 3.0
    min_word_count: int = 20
    stealth: bool = False
    blocked_domains: list[str] = Field(default_factory=list)


class SearchConfig(BaseModel):
    topic: str
    query_variants: list[str] = Field(default_factory=list)
    queries_count: int = 8
    results_per_query: int = 15
    exclude_domains: list[str] = Field(default_factory=lambda: [
        "pinterest.com", "facebook.com", "instagram.com", "tiktok.com",
    ])
    output_dir: str = "./output"
    delay: float = 1.5
    concurrency: int = 3
    pruning_threshold: float = 0.30
    markdown_format: Literal["fit", "raw", "citations"] = "fit"
    min_word_count: int = 30
    max_pages: int = 100
    crawl_depth: int = 0
    css_selector: str | None = None
    stealth: bool = True
    verbose: bool = False


class UrlListConfig(BaseModel):
    """Config for crawling a pre-collected list of URLs (e.g. claude_links_collection.json)."""
    url_file: str                    # Path to JSON file with url entries
    output_dir: str = "./output"
    delay: float = 1.5
    max_range: float = 0.5
    concurrency: int = 5
    headless: bool = True
    auto_classify: bool = True       # Auto-detect domain type and tune settings
    markdown_format: Literal["fit", "raw", "citations"] = "fit"
    min_word_count: int = 50
    max_retries: int = 2
    retry_delay: float = 3.0
    stealth: bool = True
    verbose: bool = False
    cache_mode: Literal["bypass", "enabled", "disabled", "read_only", "write_only"] = "enabled"
    # Global overrides (empty = use auto-tuned per-domain defaults)
    global_query: str = "claude code"
    global_depth: int | None = None  # None = use domain profile default
    global_max_pages: int | None = None
    blocked_domains: list[str] = Field(default_factory=lambda: [
        "pinterest.com", "facebook.com", "instagram.com", "tiktok.com",
    ])
    # Resume support
    progress_file: str = ""          # Path to progress JSON; empty = auto-generate
    resume: bool = False             # Skip URLs already in progress file


def load_url_list_config(path: str | Path) -> UrlListConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return UrlListConfig(**data)


def load_search_config(path: str | Path) -> SearchConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return SearchConfig(**data)


def load_config(path: str | Path) -> CrawlJobConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return CrawlJobConfig(**data)


def config_from_cli_args(
    urls: list[str],
    depth: int = 3,
    max_pages: int = 100,
    output: str = "./output",
    delay: float = 1.0,
    concurrency: int = 5,
    format: str = "fit",
    verbose: bool = False,
    css_selector: str | None = None,
    min_word_count: int = 20,
) -> CrawlJobConfig:
    sites = [
        SiteConfig(url=u, max_depth=depth, max_pages=max_pages, css_selector=css_selector)
        for u in urls
    ]
    return CrawlJobConfig(
        sites=sites,
        output_dir=output,
        delay=delay,
        concurrency=concurrency,
        markdown_format=format,  # type: ignore[arg-type]
        verbose=verbose,
        min_word_count=min_word_count,
    )
