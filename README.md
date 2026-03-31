# crawl4ai-cli

A CLI tool that crawls websites and saves clean markdown files optimized for LLM and RAG consumption. Built on [crawl4ai](https://github.com/unclecode/crawl4ai).

## Features

- **Deep crawling** — BFS traversal from root URLs with configurable depth and page limits
- **Clean markdown** — Multi-layer boilerplate removal: HTML tag exclusion, content pruning, and pattern stripping
- **CSS selector targeting** — Extract only the main content area (`main`, `article`, `.content`)
- **JavaScript rendering** — Full SPA/JS support with `networkidle` wait and configurable timeouts
- **Smart deduplication** — URL normalization, locale path dedup, and content hash dedup
- **Quality gate** — Automatically skips low-quality pages (loading screens, login forms, empty content)
- **Retry with backoff** — Automatic retries on timeout, 429, and 503 errors
- **YAML frontmatter** — Each file includes source URL, title, crawl depth, timestamp, and word count
- **Rich summary table** — Per-site stats with quality breakdown after each crawl
- **JSON manifest** — Complete index with quality summary, duration, and per-page metadata
- **Multi-site support** — Crawl multiple sites in one job via YAML config

## Installation

Requires Python 3.11+.

```bash
git clone <repo-url> && cd crawl4AI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After installation, install the browser binary used by crawl4ai:

```bash
playwright install chromium
```

## Quick Start

### Crawl a single site

```bash
crawl4ai-cli crawl https://docs.example.com --depth 2 --max-pages 50
```

### Crawl with CSS selector for clean output

```bash
crawl4ai-cli crawl https://docs.example.com --css-selector main --depth 2
```

### Crawl multiple URLs

```bash
crawl4ai-cli crawl https://docs.example.com https://blog.example.com --depth 2
```

### Crawl from a config file

```bash
crawl4ai-cli crawl --config crawl_config.yaml
```

### Generate a starter config

```bash
crawl4ai-cli init-config -o my_config.yaml
```

## CLI Reference

### `crawl4ai-cli crawl`

```
Usage: crawl4ai-cli crawl [OPTIONS] [URLS]...

Arguments:
  URLS                         Root URL(s) to crawl

Options:
  -c, --config PATH            YAML config file (overrides all other options)
  -d, --depth INT              Max crawl depth [default: 3]
  -n, --max-pages INT          Max pages per site [default: 100]
  -o, --output TEXT            Output directory [default: ./output]
  --delay FLOAT                Mean delay between requests in seconds [default: 1.0]
  --concurrency INT            Max concurrent crawls [default: 5]
  -f, --format TEXT            Markdown format: fit, raw, citations [default: fit]
  -s, --css-selector TEXT      CSS selector for main content (e.g. main, article)
  --min-words INT              Minimum word count to save a page [default: 20]
  --clean                      Delete output directory before crawling
  -v, --verbose                Enable debug logging
```

### `crawl4ai-cli init-config`

```
Usage: crawl4ai-cli init-config [OPTIONS]

Options:
  -o, --output PATH            Output path for config file [default: crawl_config.yaml]
```

## YAML Config

For multi-site crawls or reusable configurations, use a YAML file:

```yaml
output_dir: ./output
delay: 1.5
concurrency: 3
pruning_threshold: 0.48
markdown_format: fit
min_word_count: 20
max_retries: 2
retry_delay: 3.0

sites:
  - url: https://docs.example.com
    max_depth: 3
    max_pages: 200
    css_selector: main
    wait_until: networkidle
    page_timeout: 60
    include_patterns:
      - "*/docs/*"
      - "*/guide/*"
    exclude_patterns:
      - "*/api-reference/*"
    skip_locale_duplicates: true
    deduplicate_content: true

  - url: https://blog.example.com
    max_depth: 2
    max_pages: 50
    css_selector: article
```

### Global Options

| Option | Default | Description |
|---|---|---|
| `output_dir` | `./output` | Directory for markdown files and manifest |
| `delay` | `1.0` | Mean delay between requests (seconds) |
| `max_range` | `0.5` | Random jitter added to delay |
| `concurrency` | `5` | Max concurrent browser sessions |
| `headless` | `true` | Run browser in headless mode |
| `cache_mode` | `bypass` | `bypass`, `enabled`, or `disabled` |
| `pruning_threshold` | `0.48` | Content filter aggressiveness (0.0-1.0, higher = stricter) |
| `markdown_format` | `fit` | `fit` (cleanest), `raw` (full page), `citations` (numbered refs) |
| `min_word_count` | `20` | Skip pages with fewer words than this |
| `max_retries` | `2` | Retry count for timeout/429/503 errors |
| `retry_delay` | `3.0` | Base delay between retries (doubles each attempt) |
| `generate_manifest` | `true` | Write `manifest.json` after crawling |

### Site Options

| Option | Default | Description |
|---|---|---|
| `url` | *(required)* | Root URL to start crawling from |
| `max_depth` | `3` | How many link-levels deep to follow |
| `max_pages` | `100` | Maximum pages to crawl for this site |
| `css_selector` | `null` | CSS selector for main content area (e.g. `main`, `article`, `.content`) |
| `wait_until` | `networkidle` | Page load strategy: `networkidle`, `domcontentloaded`, `load` |
| `wait_for` | `null` | CSS selector to wait for before extracting (e.g. `.article-body`) |
| `page_timeout` | `60` | Seconds to wait for page load |
| `include_patterns` | `[]` | Glob patterns — only crawl URLs matching these |
| `exclude_patterns` | `[]` | Glob patterns — skip URLs matching these |
| `domain_only` | `true` | Stay on the same domain |
| `skip_locale_duplicates` | `true` | Skip pages that differ only by locale prefix (e.g. `/fr-fr/`, `/de-de/`) |
| `deduplicate_content` | `true` | Skip pages with near-identical content (hash-based) |

## Performance Tuning by Use Case

### Documentation sites (best quality)

Static doc sites like ReadTheDocs, Docusaurus, MkDocs. These render fast and have predictable structure.

```yaml
output_dir: ./docs-kb
delay: 0.5
concurrency: 5
pruning_threshold: 0.48
markdown_format: fit
min_word_count: 30

sites:
  - url: https://docs.example.com
    max_depth: 4
    max_pages: 500
    css_selector: main             # most doc sites use <main> for content
    wait_until: domcontentloaded   # static sites don't need networkidle
    page_timeout: 30               # fast sites, short timeout
    include_patterns:
      - "*/docs/*"
      - "*/guide/*"
      - "*/tutorial/*"
    exclude_patterns:
      - "*/changelog/*"
      - "*/api-reference/*"        # API refs are often auto-generated noise
      - "*/_print/*"               # print-friendly duplicates
    skip_locale_duplicates: true
    deduplicate_content: true
```

```bash
# CLI equivalent
crawl4ai-cli crawl https://docs.example.com \
  --depth 4 --max-pages 500 \
  --css-selector main \
  --min-words 30 \
  --delay 0.5 \
  --clean -o ./docs-kb
```

**Why these settings:**
- `css_selector: main` — strips sidebar, nav, footer at HTML level (biggest quality win)
- `domcontentloaded` — doc sites are mostly static, no need to wait for JS
- `page_timeout: 30` — aggressive timeout catches broken pages fast
- `pruning_threshold: 0.48` — balanced filtering; raise to 0.6 if output still has noise
- `min_word_count: 30` — skip stub pages and redirects
- High depth + pages — doc sites are deep but well-structured

### JavaScript-heavy sites (SPAs, React/Next.js)

Sites like platform dashboards, modern blogs, or any SPA that renders content client-side.

```yaml
output_dir: ./spa-kb
delay: 2.0                        # give the server breathing room
concurrency: 3                    # fewer concurrent sessions = less memory
pruning_threshold: 0.45
markdown_format: fit
min_word_count: 20
max_retries: 3                    # SPAs are flaky, retry more
retry_delay: 5.0

sites:
  - url: https://platform.example.com/docs
    max_depth: 3
    max_pages: 200
    css_selector: "[role='main']"  # or article, .content, #main-content
    wait_until: networkidle        # critical — wait for JS to finish rendering
    wait_for: ".article-body"      # wait for this specific element to appear
    page_timeout: 90               # SPAs can be slow, be patient
    skip_locale_duplicates: true
    deduplicate_content: true
```

```bash
# CLI equivalent
crawl4ai-cli crawl https://platform.example.com/docs \
  --depth 3 --max-pages 200 \
  --css-selector "[role='main']" \
  --delay 2.0 --concurrency 3 \
  --min-words 20 \
  --clean -o ./spa-kb
```

**Why these settings:**
- `networkidle` — waits until no network activity for 500ms (essential for SPAs)
- `wait_for` — extra safety: don't extract until the content element exists in DOM
- `page_timeout: 90` — SPAs with lazy loading need time
- `concurrency: 3` — each browser tab uses ~100MB RAM; fewer tabs = stable crawl
- `max_retries: 3` — JS rendering is non-deterministic, retries help
- `delay: 2.0` — respect the server, avoid rate limiting

### Blog / news sites (high volume)

Content-heavy sites with many articles. Goal: maximize pages crawled per minute.

```yaml
output_dir: ./blog-kb
delay: 1.0
concurrency: 5
pruning_threshold: 0.5            # slightly stricter to remove ads/promos
markdown_format: fit
min_word_count: 50                # skip short teasers and category pages
max_retries: 2
cache_mode: enabled               # cache for re-runs (saves time on retries)

sites:
  - url: https://blog.example.com
    max_depth: 2                   # blogs are shallow: index -> article
    max_pages: 300
    css_selector: article          # most blogs wrap content in <article>
    wait_until: domcontentloaded
    page_timeout: 30
    exclude_patterns:
      - "*/tag/*"                  # tag index pages
      - "*/category/*"            # category index pages
      - "*/author/*"              # author profile pages
      - "*/page/*"                # pagination pages
      - "*/feed/*"
      - "*/comments/*"
    skip_locale_duplicates: true
    deduplicate_content: true
```

```bash
# CLI equivalent
crawl4ai-cli crawl https://blog.example.com \
  --depth 2 --max-pages 300 \
  --css-selector article \
  --min-words 50 \
  --delay 1.0 --concurrency 5 \
  --clean -o ./blog-kb
```

**Why these settings:**
- `css_selector: article` — blogs consistently use `<article>` tags
- `min_word_count: 50` — filters out index pages, teasers, and tag listings
- `max_depth: 2` — blogs are flat: homepage/listing -> article (no deeper)
- `pruning_threshold: 0.5` — slightly stricter to catch ad blocks and promo banners
- Aggressive exclude patterns — skip taxonomy pages that add no LLM value
- `cache_mode: enabled` — useful when re-running to fill gaps

### Reddit / forums (community content)

User-generated content with complex layouts and lots of UI chrome.

```yaml
output_dir: ./reddit-kb
delay: 2.0
concurrency: 3
pruning_threshold: 0.45
markdown_format: fit
min_word_count: 30
max_retries: 2

sites:
  - url: https://www.reddit.com/r/ExampleSub/
    max_depth: 1                   # 0 = listing, 1 = individual threads
    max_pages: 100
    css_selector: "[id='main-content']"
    wait_until: networkidle
    page_timeout: 60
    include_patterns:
      - "*/comments/*"            # only crawl actual threads, not UI pages
    exclude_patterns:
      - "*/login*"
      - "*/register*"
      - "*/settings/*"
      - "*/mod/*"
      - "*/wiki/*"
    skip_locale_duplicates: true
    deduplicate_content: true
```

**Why these settings:**
- `css_selector: "[id='main-content']"` — Reddit wraps post content here, strips all nav/sidebar
- `include_patterns: "*/comments/*"` — only save actual discussion threads
- `max_depth: 1` — listing page -> thread is one hop; going deeper hits user profiles
- `networkidle` — Reddit is a SPA, needs full JS rendering
- Extensive exclude patterns — Reddit has many UI-only routes

### Knowledge base builder (multi-source)

Crawl multiple sources into a single knowledge base for RAG.

```yaml
output_dir: ./knowledge-base
delay: 1.5
concurrency: 3
pruning_threshold: 0.48
markdown_format: citations        # numbered refs for source attribution in RAG
min_word_count: 30
max_retries: 2
retry_delay: 3.0

sites:
  # Official docs
  - url: https://docs.example.com
    max_depth: 4
    max_pages: 300
    css_selector: main
    wait_until: domcontentloaded
    page_timeout: 30

  # Blog / tutorials
  - url: https://blog.example.com
    max_depth: 2
    max_pages: 100
    css_selector: article
    wait_until: domcontentloaded
    page_timeout: 30
    exclude_patterns:
      - "*/tag/*"
      - "*/category/*"

  # Community discussions
  - url: https://github.com/example/repo/discussions
    max_depth: 1
    max_pages: 50
    css_selector: ".js-discussion"
    wait_until: networkidle
    page_timeout: 60

  # API changelog
  - url: https://docs.example.com/changelog
    max_depth: 1
    max_pages: 20
    css_selector: main
    wait_until: domcontentloaded
```

**Why these settings:**
- `citations` format — RAG pipelines can trace answers back to source URLs
- Multiple sources with different strategies — each site type gets optimal config
- Lower page limits per source — balanced coverage across sources
- `min_word_count: 30` — ensures every page adds real content to the knowledge base

### Quick test (verify a site works)

```bash
crawl4ai-cli crawl https://example.com --depth 1 --max-pages 3 --clean -v
```

### Maximum speed (trusted sites only)

```bash
crawl4ai-cli crawl https://your-own-site.com \
  --depth 3 --max-pages 500 \
  --delay 0.2 --concurrency 10 \
  --css-selector main \
  --clean -o ./fast-crawl
```

**Warning:** Only use low delay + high concurrency on sites you own or have permission to crawl aggressively.

## Output Structure

```
output/
├── docs.example.com/
│   ├── guide/
│   │   ├── setup.md
│   │   └── configuration.md
│   └── index.md
├── blog.example.com/
│   └── posts/
│       └── my-article.md
└── manifest.json
```

### Markdown Files

Each `.md` file includes YAML frontmatter followed by the cleaned page content:

```markdown
---
source_url: https://docs.example.com/guide/setup
title: "Setup Guide"
crawl_depth: 2
crawled_at: 2026-03-31T14:22:00Z
word_count: 1847
---

# Setup Guide

Page content here, with boilerplate removed...
```

### Markdown Formats

| Format | Description | Best for |
|---|---|---|
| `fit` | Pruned of navigation, ads, and boilerplate | LLM context, RAG pipelines |
| `raw` | Full page converted to markdown | Archival, complete content |
| `citations` | Links replaced with numbered `[1]` references | RAG with source attribution |

### Manifest

`manifest.json` provides a machine-readable index with quality stats:

```json
{
  "generated_at": "2026-03-31T14:30:00Z",
  "tool_version": "0.2.0",
  "duration_seconds": 45.2,
  "quality_summary": {
    "total_pages": 50,
    "success": 47,
    "failed": 1,
    "empty_content": 1,
    "low_quality": 1,
    "total_words": 89420
  },
  "sites": [
    {
      "root_url": "https://docs.example.com",
      "pages_crawled": 47,
      "pages_failed": 1,
      "pages_skipped": 3,
      "max_depth_reached": 3,
      "total_retries": 1
    }
  ],
  "pages": [
    {
      "url": "https://docs.example.com/guide/setup",
      "file": "docs.example.com/guide/setup.md",
      "title": "Setup Guide",
      "depth": 2,
      "word_count": 1847,
      "status": "success",
      "crawled_at": "2026-03-31T14:22:00Z"
    }
  ]
}
```

## How It Works

1. **Browser launch** — Starts a headless Chromium instance via Playwright
2. **BFS deep crawl** — Follows internal links from the root URL, breadth-first
3. **URL deduplication** — Normalizes URLs (strips tracking params, trailing slashes) and skips locale variants
4. **JavaScript rendering** — Waits for `networkidle` (or custom selector) before extracting
5. **HTML filtering** — Removes `<nav>`, `<header>`, `<footer>`, `<aside>`, `<form>` tags and cookie/banner selectors
6. **CSS targeting** — If `css_selector` is set, extracts only the matched region
7. **Content pruning** — `PruningContentFilter` removes remaining boilerplate by text density analysis
8. **Quality gate** — Skips pages below `min_word_count` or matching low-quality indicators
9. **Content dedup** — Hashes first 500 words to skip near-duplicate pages
10. **Boilerplate stripping** — Post-processes markdown to remove "Skip to content" links, orphan links, copyright lines
11. **Streaming write** — Markdown files are written to disk as each page completes
12. **Retry on failure** — Automatically retries on timeout/429/503 with exponential backoff
13. **Manifest generation** — JSON index with quality summary written after all sites finish

## Tips

- **Start with `--css-selector`** — This single option often eliminates 90% of noise. Inspect the target site and find the main content wrapper (`main`, `article`, `.content`, `#main-content`).
- **Use `--clean`** — Prevents stale files from previous runs mixing with new output.
- **Check the manifest** — `quality_summary` tells you how many pages were useful vs skipped. If `low_quality` is high, lower `min_word_count` or check if `css_selector` is too narrow.
- **Tune `pruning_threshold`** — If output has too much noise, raise to 0.55-0.65. If content is being cut, lower to 0.35-0.40.
- **Use `cache_mode: enabled`** for iterative runs — avoids re-fetching pages you already have.
- **Use `--verbose`** for debugging — shows per-page crawl4ai logs, skipped URLs, and retry attempts.

## License

MIT
