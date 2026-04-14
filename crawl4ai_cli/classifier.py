"""Auto domain classification and adaptive crawl settings.

Classifies URLs into domain types (docs, github, reddit, blog, forum, video,
social, other) and returns optimal crawl parameters for each type.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

DomainType = Literal[
    "docs", "github", "reddit", "forum", "blog",
    "video", "social", "news", "other",
]

# ── Domain detection rules ──────────────────────────────────────────────

_DOCS_DOMAINS = {
    "docs.anthropic.com", "code.claude.com", "claude.com",
    "docs.python.org", "developer.mozilla.org", "devdocs.io",
    "readthedocs.io", "readthedocs.org", "gitbook.io",
}

_DOCS_PATH_PATTERNS = [
    re.compile(r"/docs?(/|$)", re.IGNORECASE),
    re.compile(r"/guide(/|$)", re.IGNORECASE),
    re.compile(r"/reference(/|$)", re.IGNORECASE),
    re.compile(r"/api-?(?:reference|docs?|guide)(/|$)", re.IGNORECASE),
    re.compile(r"/manual(/|$)", re.IGNORECASE),
    re.compile(r"/quickstart", re.IGNORECASE),
    re.compile(r"/getting-started", re.IGNORECASE),
]

_GITHUB_DOMAINS = {"github.com", "raw.githubusercontent.com", "gist.github.com"}

_REDDIT_DOMAINS = {"reddit.com", "www.reddit.com", "old.reddit.com"}

_FORUM_DOMAINS = {
    "stackoverflow.com", "stackexchange.com",
    "community.anthropic.com", "discuss.python.org",
    "news.ycombinator.com", "lobste.rs",
    "discourse.org",
}

_FORUM_PATH_PATTERNS = [
    re.compile(r"/forum(/|$)", re.IGNORECASE),
    re.compile(r"/community(/|$)", re.IGNORECASE),
    re.compile(r"/discuss(ion)?(/|$)", re.IGNORECASE),
    re.compile(r"/questions?(/|$)", re.IGNORECASE),
]

_VIDEO_DOMAINS = {
    "youtube.com", "www.youtube.com", "youtu.be",
    "vimeo.com", "twitch.tv",
}

_SOCIAL_DOMAINS = {
    "twitter.com", "x.com", "linkedin.com",
    "facebook.com", "instagram.com", "tiktok.com",
    "pinterest.com", "threads.net",
}

_NEWS_DOMAINS = {
    "techcrunch.com", "theverge.com", "arstechnica.com",
    "wired.com", "venturebeat.com", "thenewstack.io",
}

_BLOG_PATH_PATTERNS = [
    re.compile(r"/blog(/|$)", re.IGNORECASE),
    re.compile(r"/posts?(/|$)", re.IGNORECASE),
    re.compile(r"/article(/|$)", re.IGNORECASE),
    re.compile(r"/tutorial(/|$)", re.IGNORECASE),
]

_BLOG_DOMAINS = {
    "medium.com", "dev.to", "hashnode.dev",
    "substack.com", "wordpress.com", "blogger.com",
    "ghost.io",
}


@dataclass(frozen=True)
class PlatformConfig:
    """Platform-specific crawl settings for known blog/forum platforms."""
    css_selector: str = ""
    js_code: str = ""
    wait_for: str = ""


@dataclass(frozen=True)
class DomainProfile:
    """Optimal crawl settings for a domain type."""
    domain_type: DomainType
    max_depth: int
    max_pages: int
    crawl_strategy: str          # "bfs" | "best_first"
    score_threshold: float       # URL scorer threshold
    bm25_threshold: float        # BM25 content filter threshold
    pruning_threshold: float     # Pruning content filter threshold
    content_filter: str          # "bm25" | "pruning"
    domain_only: bool            # Stay on same domain?
    page_timeout: int            # Seconds
    wait_until: str              # "networkidle" | "domcontentloaded"
    include_external: bool       # Follow external links?
    css_selector: str = ""      # CSS selector to extract specific content


# ── Profile definitions ─────────────────────────────────────────────────

DOMAIN_PROFILES: dict[DomainType, DomainProfile] = {
    "docs": DomainProfile(
        domain_type="docs",
        max_depth=3,
        max_pages=80,
        crawl_strategy="bfs",
        score_threshold=0.0,       # Docs pages are all relevant within domain
        bm25_threshold=0.8,        # Lenient — docs content is dense and valuable
        pruning_threshold=0.48,
        content_filter="bm25",
        domain_only=True,
        page_timeout=60,
        wait_until="networkidle",
        include_external=False,
    ),
    "github": DomainProfile(
        domain_type="github",
        max_depth=0,               # Just crawl the page itself (README)
        max_pages=1,
        crawl_strategy="bfs",
        score_threshold=0.0,
        bm25_threshold=1.0,
        pruning_threshold=0.30,
        content_filter="pruning",  # GitHub READMEs work better with pruning
        domain_only=True,
        page_timeout=30,
        wait_until="domcontentloaded",
        include_external=False,
        css_selector="article.markdown-body",
    ),
    "reddit": DomainProfile(
        domain_type="reddit",
        max_depth=0,               # Just the thread
        max_pages=1,
        crawl_strategy="bfs",
        score_threshold=0.0,
        bm25_threshold=1.0,
        pruning_threshold=0.30,
        content_filter="pruning",
        domain_only=True,
        page_timeout=30,
        wait_until="domcontentloaded",
        include_external=False,
        css_selector="[id='main-content']",
    ),
    "forum": DomainProfile(
        domain_type="forum",
        max_depth=0,               # Just the thread/question
        max_pages=1,
        crawl_strategy="bfs",
        score_threshold=0.0,
        bm25_threshold=1.0,
        pruning_threshold=0.35,
        content_filter="pruning",
        domain_only=True,
        page_timeout=30,
        wait_until="domcontentloaded",
        include_external=False,
        css_selector="#question, .answer, .post-stream",
    ),
    "blog": DomainProfile(
        domain_type="blog",
        max_depth=1,               # May have multi-part posts
        max_pages=5,
        crawl_strategy="best_first",
        score_threshold=0.3,
        bm25_threshold=0.5,
        pruning_threshold=0.40,
        content_filter="pruning",  # Pruning keeps 98% of content; BM25 too aggressive for varied blogs
        domain_only=True,
        page_timeout=30,
        wait_until="domcontentloaded",
        include_external=False,
    ),
    "video": DomainProfile(
        domain_type="video",
        max_depth=0,
        max_pages=1,
        crawl_strategy="bfs",
        score_threshold=0.0,
        bm25_threshold=1.0,
        pruning_threshold=0.30,
        content_filter="pruning",
        domain_only=False,
        page_timeout=20,
        wait_until="domcontentloaded",
        include_external=False,
    ),
    "social": DomainProfile(
        domain_type="social",
        max_depth=0,
        max_pages=1,
        crawl_strategy="bfs",
        score_threshold=0.0,
        bm25_threshold=1.0,
        pruning_threshold=0.30,
        content_filter="pruning",
        domain_only=False,
        page_timeout=20,
        wait_until="domcontentloaded",
        include_external=False,
    ),
    "news": DomainProfile(
        domain_type="news",
        max_depth=0,
        max_pages=1,
        crawl_strategy="bfs",
        score_threshold=0.0,
        bm25_threshold=1.0,
        pruning_threshold=0.35,
        content_filter="bm25",
        domain_only=True,
        page_timeout=30,
        wait_until="domcontentloaded",
        include_external=False,
        css_selector=".comment-tree, .fatitem",
    ),
    "other": DomainProfile(
        domain_type="other",
        max_depth=1,
        max_pages=5,
        crawl_strategy="best_first",
        score_threshold=0.3,
        bm25_threshold=0.5,
        pruning_threshold=0.40,
        content_filter="pruning",  # Pruning keeps more content; BM25 too aggressive for diverse sites
        domain_only=True,
        page_timeout=30,
        wait_until="domcontentloaded",
        include_external=False,
    ),
}


# Platform-specific crawl configs (applied by domain match, not domain type)
PLATFORM_CONFIGS: dict[str, PlatformConfig] = {
    "medium.com": PlatformConfig(css_selector="article"),
    "dev.to": PlatformConfig(css_selector="#article-body"),
    "substack.com": PlatformConfig(css_selector=".post-content, .body.markup"),
    "ghost.io": PlatformConfig(css_selector=".post-content, .gh-content"),
    "hashnode.dev": PlatformConfig(css_selector=".blog-content-wrapper"),
    "stackoverflow.com": PlatformConfig(
        css_selector="#question, .answer",
        js_code="document.querySelectorAll('.js-show-link.comments-link').forEach(b => b.click())",
        wait_for="css:.comment-body",
    ),
    "reddit.com": PlatformConfig(
        js_code="document.querySelectorAll('[id*=\"-see-more\"]').forEach(b => b.click())",
    ),
}


def classify_url(url: str) -> DomainType:
    """Classify a URL into a domain type based on domain and path patterns."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    # Strip www. for matching
    bare_domain = domain.removeprefix("www.")

    # Exact domain matches (highest priority)
    if bare_domain in _SOCIAL_DOMAINS or domain in _SOCIAL_DOMAINS:
        return "social"

    if bare_domain in _VIDEO_DOMAINS or domain in _VIDEO_DOMAINS:
        return "video"

    if bare_domain in _REDDIT_DOMAINS or domain in _REDDIT_DOMAINS:
        return "reddit"

    if bare_domain in _GITHUB_DOMAINS or domain in _GITHUB_DOMAINS:
        return "github"

    if bare_domain in _FORUM_DOMAINS or domain in _FORUM_DOMAINS:
        return "forum"

    if bare_domain in _NEWS_DOMAINS or domain in _NEWS_DOMAINS:
        return "news"

    # Blog detection: domain/subdomain match BEFORE docs path patterns
    # (substack.com/p/getting-started shouldn't match docs /getting-started pattern)
    if bare_domain in _BLOG_DOMAINS or domain in _BLOG_DOMAINS:
        return "blog"
    if any(bare_domain.endswith(f".{d}") for d in {"medium.com", "substack.com", "hashnode.dev", "ghost.io"}):
        return "blog"

    # Docs detection: domain match OR path pattern
    if bare_domain in _DOCS_DOMAINS or domain in _DOCS_DOMAINS:
        return "docs"
    if any(bare_domain.endswith(f".{d}") for d in {"readthedocs.io", "readthedocs.org", "gitbook.io"}):
        return "docs"
    if any(p.search(path) for p in _DOCS_PATH_PATTERNS):
        return "docs"

    # Blog path patterns (after docs, so /docs/blog doesn't match as blog)
    if any(p.search(path) for p in _BLOG_PATH_PATTERNS):
        return "blog"

    # Forum path patterns (after exact domain check)
    if any(p.search(path) for p in _FORUM_PATH_PATTERNS):
        return "forum"

    return "other"


def get_profile(url: str) -> DomainProfile:
    """Get the optimal crawl profile for a URL."""
    domain_type = classify_url(url)
    return DOMAIN_PROFILES[domain_type]


def get_platform_config(url: str) -> PlatformConfig:
    """Get platform-specific crawl config for known platforms."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    for platform, config in PLATFORM_CONFIGS.items():
        if domain == platform or domain.endswith(f".{platform}"):
            return config
    return PlatformConfig()


def get_platform_selector(url: str) -> str:
    """Get platform-specific CSS selector (backward compat wrapper)."""
    return get_platform_config(url).css_selector


def classify_urls(urls: list[str]) -> dict[DomainType, list[str]]:
    """Group URLs by their domain type classification."""
    groups: dict[DomainType, list[str]] = {}
    for url in urls:
        dt = classify_url(url)
        groups.setdefault(dt, []).append(url)
    return groups
