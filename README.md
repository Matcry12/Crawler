# crawl4ai-cli

A CLI tool for building large-scale knowledge bases from the web. Crawls hundreds of URLs, auto-classifies domains, and outputs clean markdown optimized for LLM and RAG consumption. Built on [crawl4ai](https://github.com/unclecode/crawl4ai).

Built for the **Claude Code Knowledge Base** project: 882 URLs across 20 topics, 1,859 clean files, 3.5M words.

## Features

### Crawling
- **Deep crawling** — BFS traversal with configurable depth and page limits
- **Auto domain classification** — Detects docs, GitHub, Reddit, forums, blogs, video, social, news sites and applies optimal crawl settings per type
- **Platform-specific selectors** — Built-in CSS selectors for Medium, Dev.to, Substack, Ghost, Hashnode, StackOverflow, Reddit, GitHub, and more
- **JavaScript rendering** — Full SPA/JS support with `networkidle` wait and configurable timeouts
- **Stealth mode** — Random user agents, navigator override, and anti-bot evasion
- **Retry with backoff** — Automatic retries on timeout, 429, and 503 errors

### Content Quality
- **Clean markdown** — Multi-layer boilerplate removal: HTML tag exclusion, content pruning, and pattern stripping
- **CSS selector targeting** — Extract only the main content area (`main`, `article`, `.content`)
- **BM25 relevance filtering** — Score pages against a query to keep only relevant content
- **Smart deduplication** — URL normalization, locale path dedup, and content hash dedup
- **Quality gate** — Skips low-quality pages (loading screens, login forms, empty content)

### Scale & Workflow
- **Bulk URL crawling** — Crawl hundreds of URLs from a JSON collection file with `crawl-urls`
- **Topic search** — Search DuckDuckGo for a topic, then crawl all found URLs with `search`
- **Resume support** — Progress tracking with `--resume` to continue interrupted crawls
- **Smart update** — Re-crawl only missing or low-quality pages with `--smart-update`
- **Re-crawl short pages** — Automatically find and retry pages with thin content via `recrawl-short`
- **Reprocess without re-crawling** — Re-run writer cleanup on existing files with `reprocess`
- **Caching** — Cache modes (enabled, bypass, read_only, write_only) for incremental re-runs
- **YAML frontmatter** — Each file includes source URL, title, crawl depth, timestamp, and word count
- **Rich progress UI** — Live progress bars, domain classification tables, and per-site stats
- **JSON manifest** — Complete index with quality summary, duration, and per-page metadata

## Installation

Requires Python 3.11+.

```bash
git clone <repo-url> && cd crawl4AI
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After installation, install the browser binary:

```bash
playwright install chromium
```

## Commands

### `crawl` — Crawl sites by URL

Crawl one or more root URLs with BFS deep crawling.

```bash
# Single site
crawl4ai-cli crawl https://docs.example.com --depth 2 --max-pages 50

# With CSS selector for clean output
crawl4ai-cli crawl https://docs.example.com --css-selector main --depth 2

# Multiple URLs
crawl4ai-cli crawl https://docs.example.com https://blog.example.com --depth 2

# From a config file
crawl4ai-cli crawl --config crawl_config.yaml
```

### `search` — Search and crawl a topic

Search DuckDuckGo for a topic, then crawl all discovered URLs.

```bash
# Search and crawl
crawl4ai-cli search "claude code tutorial" -o ./claude-kb

# More queries, more results
crawl4ai-cli search "claude code best practices" --queries 12 --results-per-query 20

# With depth (follow links from found pages)
crawl4ai-cli search "MCP server setup" --crawl-depth 1 --max-pages 200
```

### `crawl-urls` — Bulk crawl from a JSON collection

Crawl hundreds of URLs from a structured JSON file. Auto-classifies each domain and applies optimal settings.

```bash
# Basic usage
crawl4ai-cli crawl-urls claude_links_collection.json -o ./claude-kb

# Resume an interrupted crawl
crawl4ai-cli crawl-urls claude_links_collection.json -o ./claude-kb --resume

# Only crawl missing or low-quality pages
crawl4ai-cli crawl-urls claude_links_collection.json -o ./claude-kb --smart-update

# Re-crawl pages older than 14 days
crawl4ai-cli crawl-urls claude_links_collection.json -o ./claude-kb --smart-update --max-age 14

# With caching for fast re-runs
crawl4ai-cli crawl-urls claude_links_collection.json -o ./claude-kb --cache enabled
```

**JSON format:**
```json
{
  "urls": [
    {"url": "https://docs.anthropic.com/...", "title": "...", "snippet": "..."},
    {"url": "https://github.com/anthropics/...", "title": "...", "snippet": "..."}
  ]
}
```

### `recrawl-short` — Retry thin pages

Scan existing output for short pages and re-crawl them with scrolling and domain-specific selectors.

```bash
# Re-crawl pages under 100 words
crawl4ai-cli recrawl-short ./claude-kb --min-words 100

# Custom threshold
crawl4ai-cli recrawl-short ./claude-kb --min-words 200 --concurrency 5
```

### `reprocess` — Re-run cleanup without re-crawling

Re-apply writer post-processing on existing markdown files. Useful after improving the writer cleanup logic.

```bash
# Reprocess all files
crawl4ai-cli reprocess ./claude-kb

# Reprocess only one domain
crawl4ai-cli reprocess ./claude-kb --domain github.com
```

### `init-config` — Generate starter YAML

```bash
crawl4ai-cli init-config -o my_config.yaml
```

## Domain Auto-Classification

When using `crawl-urls`, each URL is automatically classified:

| Type | Examples | Strategy |
|------|----------|----------|
| **docs** | docs.anthropic.com, readthedocs.io | Deep crawl, `main` selector, fast timeout |
| **github** | github.com repos, gists | Single page, `.markdown-body` selector |
| **reddit** | reddit.com threads | Single page, `networkidle` wait |
| **forum** | stackoverflow.com, discourse | Thread-level, comment selectors |
| **blog** | medium.com, dev.to, substack | Article selector, pruning filter |
| **video** | youtube.com | Metadata only (descriptions/transcripts) |
| **social** | twitter.com, linkedin.com | Single page, stealth mode |
| **news** | techcrunch.com, theverge.com | Article selector, ad removal |
| **other** | Everything else | Balanced defaults |

Each type gets tuned: depth, max pages, CSS selector, wait strategy, timeout, and content filter.

## YAML Config

For multi-site crawls or reusable configurations:

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
| `cache_mode` | `bypass` | `bypass`, `enabled`, `read_only`, `write_only` |
| `pruning_threshold` | `0.48` | Content filter aggressiveness (0.0-1.0) |
| `markdown_format` | `fit` | `fit` (cleanest), `raw` (full), `citations` (numbered refs) |
| `min_word_count` | `20` | Skip pages with fewer words |
| `max_retries` | `2` | Retry count for timeout/429/503 errors |
| `retry_delay` | `3.0` | Base delay between retries (doubles each attempt) |

### Site Options

| Option | Default | Description |
|---|---|---|
| `url` | *(required)* | Root URL to crawl |
| `max_depth` | `3` | Link-levels deep to follow |
| `max_pages` | `100` | Maximum pages for this site |
| `css_selector` | `null` | CSS selector for main content |
| `wait_until` | `networkidle` | Page load strategy |
| `wait_for` | `null` | CSS selector to wait for before extracting |
| `page_timeout` | `60` | Seconds to wait for page load |
| `include_patterns` | `[]` | Glob patterns — only crawl matching URLs |
| `exclude_patterns` | `[]` | Glob patterns — skip matching URLs |
| `domain_only` | `true` | Stay on the same domain |
| `skip_locale_duplicates` | `true` | Skip locale variants (`/fr-fr/`, `/de-de/`) |
| `deduplicate_content` | `true` | Skip near-identical pages (hash-based) |

## Output Structure

```
claude-kb/
├── docs.anthropic.com/
│   ├── en/docs/
│   │   ├── claude-code/overview.md
│   │   └── claude-code/cli-usage.md
│   └── index.md
├── github.com/
│   └── anthropics/
│       └── claude-code/blob/main/README.md
├── www.reddit.com/
│   └── r/ClaudeAI/comments/...md
└── manifest.json
```

Each `.md` file includes YAML frontmatter:

```markdown
---
source_url: https://docs.anthropic.com/en/docs/claude-code/overview
title: "Claude Code Overview"
crawl_depth: 2
crawled_at: 2026-04-10T14:22:00Z
word_count: 1847
---

# Claude Code Overview

Page content here, with boilerplate removed...
```

## How It Works

1. **URL classification** — Each URL is classified by domain type (docs, github, blog, etc.)
2. **Profile assignment** — Optimal crawl parameters assigned per domain type
3. **Browser launch** — Headless Chromium via Playwright with stealth mode
4. **BFS deep crawl** — Follows internal links breadth-first (configurable depth)
5. **URL deduplication** — Normalizes URLs, strips tracking params, skips locale variants
6. **JavaScript rendering** — Waits for `networkidle` or custom selector
7. **HTML filtering** — Removes nav, header, footer, aside, form tags and cookie/banner selectors
8. **CSS targeting** — Extracts only the matched content region
9. **Content filtering** — BM25 relevance scoring or pruning by text density
10. **Quality gate** — Skips pages below word threshold or matching low-quality indicators
11. **Content dedup** — Hashes content to skip near-duplicates
12. **Boilerplate stripping** — Removes "Skip to content", orphan links, copyright lines
13. **Streaming write** — Files written to disk as each page completes
14. **Progress tracking** — Resume support with periodic checkpoint saves
15. **Manifest generation** — JSON index with quality summary

## Standalone Scripts

For the Claude Code KB project, additional scripts handle edge cases:

- **`crawl_blocked.py`** — Crawls Medium articles (via Freedium mirror) and PDFs (via pymupdf4llm)
- **`crawl_remaining.py`** — Crawls remaining URLs from the 882-URL collection that the CLI missed
- **`cleanup_kb.py`** — Post-processing: strips frontmatter noise, fixes code blocks, removes short/duplicate/off-topic/non-English files. Produces `claude-kb-clean/` from `claude-kb/`

## License

MIT
