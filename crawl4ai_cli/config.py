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


class CrawlJobConfig(BaseModel):
    sites: list[SiteConfig]
    output_dir: str = "./output"
    delay: float = 1.0
    max_range: float = 0.5
    concurrency: int = 5
    headless: bool = True
    cache_mode: Literal["bypass", "enabled", "disabled"] = "bypass"
    pruning_threshold: float = 0.48
    markdown_format: Literal["fit", "raw", "citations"] = "fit"
    generate_manifest: bool = True
    verbose: bool = False
    max_retries: int = 2
    retry_delay: float = 3.0
    min_word_count: int = 20


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
