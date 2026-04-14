from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
from rich.table import Table

from .config import CrawlJobConfig, SiteConfig, SearchConfig, UrlListConfig, config_from_cli_args, load_config, load_search_config, load_url_list_config
from .engine import run_job, build_url_list_job, CrawlStats, normalize_url
from .classifier import classify_url, classify_urls, get_profile, get_platform_selector
from .manifest import ManifestCollector
from .search import search_urls, build_search_job, save_search_metadata, generate_query_variants
from .writer import write_markdown

app = typer.Typer(name="crawl4ai-cli", help="Crawl websites and save clean markdown for LLM consumption.")
console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_time=False, show_path=False)],
    )


def _print_summary(all_stats: list[CrawlStats], manifest: ManifestCollector, duration: float) -> None:
    table = Table(title="Crawl Summary", show_lines=True)
    table.add_column("Site", style="bold blue")
    table.add_column("Crawled", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Skipped", justify="right", style="yellow")
    table.add_column("Retries", justify="right", style="dim")
    table.add_column("Words", justify="right")
    table.add_column("Avg Words", justify="right", style="dim")

    for stats in all_stats:
        site_pages = [p for p in manifest.pages if p.get("url", "").startswith(stats.root_url.rstrip("/"))]
        total_words = sum(p.get("word_count", 0) for p in site_pages)
        avg_words = total_words // max(stats.pages_crawled, 1)
        table.add_row(
            stats.root_url,
            str(stats.pages_crawled),
            str(stats.pages_failed),
            str(stats.pages_skipped),
            str(stats.total_retries),
            f"{total_words:,}",
            str(avg_words),
        )

    console.print()
    console.print(table)

    total_crawled = sum(s.pages_crawled for s in all_stats)
    total_failed = sum(s.pages_failed for s in all_stats)
    total_skipped = sum(s.pages_skipped for s in all_stats)

    # Quality breakdown
    status_counts = {}
    for p in manifest.pages:
        s = p.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1

    quality_parts = []
    if status_counts.get("success", 0):
        quality_parts.append(f"[green]{status_counts['success']} success[/green]")
    if status_counts.get("failed", 0):
        quality_parts.append(f"[red]{status_counts['failed']} failed[/red]")
    if status_counts.get("empty_content", 0):
        quality_parts.append(f"[yellow]{status_counts['empty_content']} empty[/yellow]")
    if status_counts.get("low_quality", 0):
        quality_parts.append(f"[yellow]{status_counts['low_quality']} low quality[/yellow]")

    console.print(f"\n[bold]Total:[/bold] {total_crawled} saved, {total_failed} failed, {total_skipped} skipped")
    if quality_parts:
        console.print(f"[bold]Quality:[/bold] {', '.join(quality_parts)}")
    console.print(f"[bold]Time:[/bold] {duration:.1f}s")


async def _run_crawl(job: CrawlJobConfig) -> None:
    manifest = ManifestCollector()
    site_tasks: dict[str, int] = {}
    start_time = time.monotonic()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]depth {task.fields[depth]}"),
        console=console,
    ) as progress:
        for site in job.sites:
            task_id = progress.add_task(site.url, total=site.max_pages, depth=0)
            site_tasks[site.url] = task_id

        async def on_result(result, site: SiteConfig, depth: int, retries: int) -> None:
            task_id = site_tasks[site.url]

            if not result.success:
                error_msg = getattr(result, "error_message", None) or "Unknown error"
                manifest.add_page(
                    url=result.url,
                    filepath=None,
                    depth=depth,
                    status="failed",
                    retries=retries,
                    error=error_msg,
                )
                progress.update(task_id, advance=1, depth=depth)
                return

            filepath, status = write_markdown(
                url=result.url,
                result=result,
                output_dir=job.output_dir,
                format=job.markdown_format,
                depth=depth,
                min_word_count=job.min_word_count,
            )

            word_count = len(str(result.markdown).split()) if result.markdown else 0
            title = ""
            if result.metadata and "title" in result.metadata:
                title = result.metadata["title"]

            manifest.add_page(
                url=result.url,
                filepath=filepath,
                depth=depth,
                word_count=word_count,
                status=status,
                title=title,
                retries=retries,
            )
            progress.update(task_id, advance=1, depth=depth)

        async def on_site_done(stats: CrawlStats) -> None:
            task_id = site_tasks[stats.root_url]
            progress.update(task_id, completed=stats.pages_crawled + stats.pages_failed)
            manifest.update_site_stats(
                root_url=stats.root_url,
                crawled=stats.pages_crawled,
                failed=stats.pages_failed,
                skipped=stats.pages_skipped,
                max_depth=stats.max_depth_reached,
                retries=stats.total_retries,
            )

        all_stats = await run_job(job, on_result=on_result, on_site_done=on_site_done)

    duration = time.monotonic() - start_time

    if job.generate_manifest:
        manifest_path = manifest.save(job.output_dir)
        console.print(f"\n[green]Manifest saved:[/green] {manifest_path}")

    _print_summary(all_stats, manifest, duration)
    console.print(f"[bold]Output:[/bold] {Path(job.output_dir).resolve()}")


@app.command()
def crawl(
    urls: Optional[list[str]] = typer.Argument(None, help="Root URL(s) to crawl"),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="YAML config file"),
    depth: int = typer.Option(3, "--depth", "-d", help="Max crawl depth"),
    max_pages: int = typer.Option(100, "--max-pages", "-n", help="Max pages per site"),
    output: str = typer.Option("./output", "--output", "-o", help="Output directory"),
    delay: float = typer.Option(1.0, "--delay", help="Mean delay between requests (seconds)"),
    concurrency: int = typer.Option(5, "--concurrency", help="Max concurrent crawls"),
    format: str = typer.Option("fit", "--format", "-f", help="Markdown format: fit, raw, citations"),
    css_selector: Optional[str] = typer.Option(None, "--css-selector", "-s", help="CSS selector for main content (e.g. 'main', 'article')"),
    min_words: int = typer.Option(20, "--min-words", help="Minimum word count to save a page"),
    clean: bool = typer.Option(False, "--clean", help="Delete output directory before crawling"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Crawl websites and save clean markdown files."""
    _setup_logging(verbose)

    if config:
        job = load_config(config)
        if verbose:
            job.verbose = True
    elif urls:
        job = config_from_cli_args(
            urls=urls,
            depth=depth,
            max_pages=max_pages,
            output=output,
            delay=delay,
            concurrency=concurrency,
            format=format,
            verbose=verbose,
            css_selector=css_selector,
            min_word_count=min_words,
        )
    else:
        console.print("[red]Error:[/red] Provide URL(s) or --config file.")
        raise typer.Exit(1)

    if clean:
        out_path = Path(job.output_dir)
        if out_path.exists():
            shutil.rmtree(out_path)
            console.print(f"[yellow]Cleaned:[/yellow] {out_path}")

    console.print(f"[bold]Crawling {len(job.sites)} site(s)...[/bold]\n")
    asyncio.run(_run_crawl(job))


@app.command()
def search(
    topic: str = typer.Argument(..., help="Topic to search for (e.g. 'claude code course')"),
    queries: int = typer.Option(8, "--queries", "-q", help="Number of query variants to generate"),
    results_per_query: int = typer.Option(15, "--results-per-query", "-r", help="Results per DDG query"),
    crawl_depth: int = typer.Option(0, "--crawl-depth", help="0=just found pages, 1=follow links"),
    max_pages: int = typer.Option(100, "--max-pages", "-n", help="Total page cap"),
    output: str = typer.Option("./output", "--output", "-o", help="Output directory"),
    format: str = typer.Option("fit", "--format", "-f", help="Markdown format: fit, raw, citations"),
    css_selector: Optional[str] = typer.Option(None, "--css-selector", "-s", help="CSS selector for main content"),
    min_words: int = typer.Option(30, "--min-words", help="Minimum word count to save a page"),
    exclude_domains: Optional[str] = typer.Option(None, "--exclude-domains", help="Comma-separated domains to skip"),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="YAML search config file"),
    clean: bool = typer.Option(False, "--clean", help="Delete output directory before crawling"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Search DuckDuckGo for a topic, then crawl all found URLs into markdown."""
    _setup_logging(verbose)

    if config:
        cfg = load_search_config(config)
    else:
        excl = [d.strip() for d in exclude_domains.split(",")] if exclude_domains else None
        cfg = SearchConfig(
            topic=topic,
            queries_count=queries,
            results_per_query=results_per_query,
            exclude_domains=excl or SearchConfig.model_fields["exclude_domains"].default_factory(),
            output_dir=output,
            delay=1.5,
            concurrency=3,
            markdown_format=format,  # type: ignore[arg-type]
            min_word_count=min_words,
            max_pages=max_pages,
            crawl_depth=crawl_depth,
            css_selector=css_selector,
            stealth=True,
            verbose=verbose,
        )

    if clean:
        out_path = Path(cfg.output_dir)
        if out_path.exists():
            shutil.rmtree(out_path)
            console.print(f"[yellow]Cleaned:[/yellow] {out_path}")

    console.print(f'[bold]Searching DuckDuckGo for "[cyan]{cfg.topic}[/cyan]"...[/bold]\n')

    # Phase 1: Search
    query_list = cfg.query_variants or generate_query_variants(cfg.topic, cfg.queries_count)
    results = search_urls(
        topic=cfg.topic,
        queries_count=cfg.queries_count,
        results_per_query=cfg.results_per_query,
        exclude_domains=cfg.exclude_domains,
        query_variants=cfg.query_variants or None,
        console=console,
    )

    if not results:
        console.print("[red]No URLs found. Try a different topic or increase --queries.[/red]")
        raise typer.Exit(1)

    # Save search metadata
    meta_path = save_search_metadata(results, cfg.topic, query_list, cfg.output_dir)
    console.print(f"[green]Search metadata saved:[/green] {meta_path}")

    # Phase 2: Crawl
    job = build_search_job(
        results=results,
        output_dir=cfg.output_dir,
        crawl_depth=cfg.crawl_depth,
        max_pages=cfg.max_pages,
        delay=cfg.delay,
        concurrency=cfg.concurrency,
        pruning_threshold=cfg.pruning_threshold,
        markdown_format=cfg.markdown_format,
        min_word_count=cfg.min_word_count,
        css_selector=cfg.css_selector,
        stealth=cfg.stealth,
        verbose=cfg.verbose,
    )

    console.print(f"\n[bold]Crawling {len(job.sites)} page(s)...[/bold]\n")
    asyncio.run(_run_crawl(job))


def _load_progress(path: Path) -> set[str]:
    """Load set of completed URLs from progress file."""
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {normalize_url(u) for u in data.get("completed_urls", [])}


def _save_progress(path: Path, completed: set[str], stats_summary: dict) -> None:
    """Save progress file with completed URLs and stats."""
    data = {
        "completed_urls": sorted(completed),
        "total_completed": len(completed),
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stats": stats_summary,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


@app.command("crawl-urls")
def crawl_urls(
    url_file: Path = typer.Argument(..., help="JSON file with URL entries (e.g. claude_links_collection.json)"),
    output: str = typer.Option("./output", "--output", "-o", help="Output directory"),
    query: str = typer.Option("claude code", "--query", "-q", help="Global relevance query for BM25 filtering"),
    depth: Optional[int] = typer.Option(None, "--depth", "-d", help="Override crawl depth (default: auto per domain type)"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", "-n", help="Override max pages per site (default: auto)"),
    concurrency: int = typer.Option(5, "--concurrency", help="Max concurrent crawls"),
    delay: float = typer.Option(1.5, "--delay", help="Mean delay between requests"),
    format: str = typer.Option("fit", "--format", "-f", help="Markdown format: fit, raw, citations"),
    min_words: int = typer.Option(20, "--min-words", help="Minimum word count to save"),
    no_classify: bool = typer.Option(False, "--no-classify", help="Disable auto domain classification"),
    resume: bool = typer.Option(False, "--resume", help="Resume from progress file, skip completed URLs"),
    smart_update: bool = typer.Option(False, "--smart-update", help="Only crawl missing or low-quality pages"),
    max_age: int = typer.Option(30, "--max-age", help="Re-crawl pages older than N days (with --smart-update)"),
    cache: str = typer.Option("enabled", "--cache", help="Cache mode: enabled, bypass, read_only, write_only"),
    clean: bool = typer.Option(False, "--clean", help="Delete output directory before crawling"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Crawl URLs from a JSON collection file with auto domain classification."""
    _setup_logging(verbose)

    if not url_file.exists():
        console.print(f"[red]Error:[/red] File not found: {url_file}")
        raise typer.Exit(1)

    # Load URL entries
    raw = json.loads(url_file.read_text(encoding="utf-8"))
    url_entries = raw.get("urls", []) if isinstance(raw, dict) else raw

    if not url_entries:
        console.print("[red]Error:[/red] No URL entries found in file.")
        raise typer.Exit(1)

    # Show classification summary
    all_urls = [e.get("url", "") for e in url_entries if e.get("url")]
    groups = classify_urls(all_urls)

    table = Table(title="Domain Classification", show_lines=False)
    table.add_column("Type", style="bold cyan")
    table.add_column("URLs", justify="right")
    table.add_column("Depth", justify="right")
    table.add_column("Pages/Site", justify="right")
    table.add_column("Strategy")
    table.add_column("Filter")

    for dt in ["docs", "github", "reddit", "forum", "blog", "video", "news", "social", "other"]:
        if dt not in groups:
            continue
        count = len(groups[dt])
        profile = get_profile(groups[dt][0])
        table.add_row(
            dt, str(count),
            str(depth if depth is not None else profile.max_depth),
            str(max_pages if max_pages is not None else profile.max_pages),
            profile.crawl_strategy,
            profile.content_filter,
        )

    console.print(table)

    # Progress / resume handling
    progress_path = Path(output) / ".crawl_progress.json"
    completed_urls: set[str] = set()
    if resume:
        completed_urls = _load_progress(progress_path)
        if completed_urls:
            console.print(f"[yellow]Resuming:[/yellow] {len(completed_urls)} URLs already completed")

    # Smart-update: skip URLs that already have good output
    if smart_update:
        from datetime import datetime, timezone
        out_path = Path(output)
        if out_path.exists():
            skipped = 0
            for fp in out_path.rglob("*.md"):
                if not fp.is_file():
                    continue
                meta = _parse_frontmatter(fp)
                source_url = meta.get("source_url", "")
                wc = meta.get("word_count", 0)
                crawled_at = meta.get("crawled_at", "")
                if not source_url:
                    continue
                # Check quality: skip if word count is good
                if wc < min_words:
                    continue
                # Check age: skip if recent enough
                if crawled_at and max_age > 0:
                    try:
                        dt = datetime.fromisoformat(crawled_at.replace("Z", "+00:00"))
                        age_days = (datetime.now(timezone.utc) - dt).days
                        if age_days > max_age:
                            continue  # too old, re-crawl
                    except (ValueError, TypeError):
                        pass
                # This URL has good, recent output — skip it
                completed_urls.add(normalize_url(source_url))
                skipped += 1
            if skipped:
                console.print(f"[yellow]Smart-update:[/yellow] skipping {skipped} URLs with good existing output")

    # Build config
    cfg = UrlListConfig(
        url_file=str(url_file),
        output_dir=output,
        delay=delay,
        concurrency=concurrency,
        auto_classify=not no_classify,
        markdown_format=format,
        min_word_count=min_words,
        global_query=query,
        global_depth=depth,
        global_max_pages=max_pages,
        stealth=True,
        verbose=verbose,
        resume=resume,
        cache_mode=cache,
    )

    job = build_url_list_job(url_entries, cfg, completed_urls)

    if not job.sites:
        console.print("[green]All URLs already completed![/green]")
        return

    if clean and not resume:
        out_path = Path(output)
        if out_path.exists():
            import shutil
            shutil.rmtree(out_path)
            console.print(f"[yellow]Cleaned:[/yellow] {out_path}")

    total_sites = len(job.sites)
    console.print(f"\n[bold]Crawling {total_sites} URL(s) (query: [cyan]{query}[/cyan])...[/bold]\n")

    # Track newly completed URLs during this run
    newly_completed: set[str] = set()
    start_time = time.monotonic()
    manifest = ManifestCollector()
    site_tasks: dict[str, int] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[dtype]}"),
        console=console,
    ) as progress:
        overall_task = progress.add_task(
            f"Crawling {total_sites} URLs", total=total_sites, dtype="",
        )

        async def _run() -> list[CrawlStats]:
            async def on_result(result, site: SiteConfig, depth_val: int, retries: int) -> None:
                url = getattr(result, "url", "") or site.url

                if not result.success:
                    error_msg = getattr(result, "error_message", None) or "Unknown error"
                    manifest.add_page(
                        url=url, filepath=None, depth=depth_val,
                        status="failed", retries=retries, error=error_msg,
                    )
                    return

                filepath, status = write_markdown(
                    url=url, result=result, output_dir=job.output_dir,
                    format=job.markdown_format, depth=depth_val,
                    min_word_count=job.min_word_count,
                )

                word_count = len(str(result.markdown).split()) if result.markdown else 0
                title = ""
                if result.metadata and "title" in result.metadata:
                    title = result.metadata["title"]

                manifest.add_page(
                    url=url, filepath=filepath, depth=depth_val,
                    word_count=word_count, status=status, title=title,
                    retries=retries,
                )

            async def on_site_done(stats: CrawlStats) -> None:
                norm = normalize_url(stats.root_url)
                newly_completed.add(norm)
                completed_urls.add(norm)
                progress.update(overall_task, advance=1)

                # Periodic progress save (every 50 sites)
                if len(newly_completed) % 50 == 0:
                    _save_progress(progress_path, completed_urls, {
                        "newly_completed": len(newly_completed),
                    })

                manifest.update_site_stats(
                    root_url=stats.root_url,
                    crawled=stats.pages_crawled,
                    failed=stats.pages_failed,
                    skipped=stats.pages_skipped,
                    max_depth=stats.max_depth_reached,
                    retries=stats.total_retries,
                )

            return await run_job(job, on_result=on_result, on_site_done=on_site_done)

        all_stats = asyncio.run(_run())

    duration = time.monotonic() - start_time

    # Save final progress
    _save_progress(progress_path, completed_urls, {
        "total_in_collection": len(url_entries),
        "crawled_this_run": len(newly_completed),
        "total_completed": len(completed_urls),
        "duration_s": round(duration, 1),
    })

    if job.generate_manifest:
        manifest_path = manifest.save(job.output_dir)
        console.print(f"\n[green]Manifest saved:[/green] {manifest_path}")

    _print_summary(all_stats, manifest, duration)
    console.print(f"[bold]Output:[/bold] {Path(job.output_dir).resolve()}")
    console.print(f"[bold]Progress:[/bold] {progress_path.resolve()} ({len(completed_urls)}/{len(url_entries)} total)")


def _parse_frontmatter(filepath: Path) -> dict:
    """Parse YAML frontmatter from a markdown file. Falls back to counting words if YAML fails."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    import yaml
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except Exception:
        meta = {}
    # Fallback: if word_count missing, count from body
    if "word_count" not in meta:
        body = text[end + 3:].strip()
        meta["word_count"] = len(body.split())
    # Extract source_url from frontmatter text if YAML parsing failed
    if "source_url" not in meta:
        for line in text[3:end].split("\n"):
            if line.startswith("source_url:"):
                meta["source_url"] = line.split(":", 1)[1].strip()
                break
    return meta


_LOGIN_WALL_PATTERNS = [
    "prove your humanity",
    "access denied",
    "403 forbidden",
    "please enable javascript",
    "this page requires javascript",
    "sign in to continue",
    "log in to continue",
]


@app.command("recrawl-short")
def recrawl_short(
    output_dir: Path = typer.Argument(..., help="Output directory with existing crawl results (e.g. ./claude-kb)"),
    min_words: int = typer.Option(100, "--min-words", help="Re-crawl pages below this word count"),
    concurrency: int = typer.Option(3, "--concurrency", help="Max concurrent crawls"),
    delay: float = typer.Option(1.5, "--delay", help="Mean delay between requests"),
    format: str = typer.Option("fit", "--format", "-f", help="Markdown format: fit, raw, citations"),
    query: str = typer.Option("claude code", "--query", "-q", help="Global relevance query"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Re-crawl pages that produced short output, using scrolling and domain-specific selectors."""
    _setup_logging(verbose)

    if not output_dir.exists():
        console.print(f"[red]Error:[/red] Directory not found: {output_dir}")
        raise typer.Exit(1)

    # Scan all markdown files for short pages
    short_pages: list[tuple[Path, str, int]] = []  # (filepath, source_url, word_count)
    all_files = list(output_dir.rglob("*.md"))

    for fp in all_files:
        meta = _parse_frontmatter(fp)
        source_url = meta.get("source_url", "")
        word_count = meta.get("word_count", 0)
        if not source_url:
            continue
        if word_count < min_words:
            # Skip known login walls
            try:
                content = fp.read_text(encoding="utf-8").lower()
            except Exception:
                continue
            if any(pat in content for pat in _LOGIN_WALL_PATTERNS):
                continue
            short_pages.append((fp, source_url, word_count))

    if not short_pages:
        console.print(f"[green]No pages found under {min_words} words. Nothing to re-crawl.[/green]")
        return

    console.print(f"[bold]Found {len(short_pages)} pages under {min_words} words to re-crawl[/bold]\n")

    # Show breakdown by domain type
    type_counts: dict[str, int] = {}
    for _, url, _ in short_pages:
        dt = classify_url(url)
        type_counts[dt] = type_counts.get(dt, 0) + 1

    table = Table(title="Short Pages by Domain Type", show_lines=False)
    table.add_column("Type", style="bold cyan")
    table.add_column("Count", justify="right")
    for dt, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        table.add_row(dt, str(count))
    console.print(table)

    # Build site configs with scrolling + domain selectors
    sites: list[SiteConfig] = []
    for fp, url, wc in short_pages:
        profile = get_profile(url)
        platform_selector = get_platform_selector(url)
        css_selector = platform_selector or profile.css_selector or None

        site = SiteConfig(
            url=url,
            query=query,
            max_depth=0,  # just the page itself
            max_pages=1,
            domain_only=True,
            css_selector=css_selector,
            wait_until=profile.wait_until,
            page_timeout=profile.page_timeout,
            domain_type=profile.domain_type,
            auto_tune=True,
        )
        sites.append(site)

    job = CrawlJobConfig(
        sites=sites,
        output_dir=str(output_dir),
        delay=delay,
        concurrency=concurrency,
        content_filter="bm25" if query else "pruning",
        markdown_format=format,
        min_word_count=30,  # lower threshold for re-crawl — keep even modest improvements
        stealth=True,
        verbose=verbose,
        generate_manifest=False,
    )

    # Track improvements
    improved = 0
    unchanged = 0
    start_time = time.monotonic()

    # Map source_url -> (original filepath, original word count)
    url_to_original: dict[str, tuple[Path, int]] = {}
    for fp, url, wc in short_pages:
        url_to_original[normalize_url(url)] = (fp, wc)

    manifest = ManifestCollector()

    async def _run() -> list[CrawlStats]:
        nonlocal improved, unchanged

        async def on_result(result, site: SiteConfig, depth_val: int, retries: int) -> None:
            nonlocal improved, unchanged
            url = getattr(result, "url", "") or site.url

            if not result.success:
                unchanged += 1
                return

            filepath, status = write_markdown(
                url=url, result=result, output_dir=str(output_dir),
                format=format, depth=depth_val, min_word_count=30,
            )

            if filepath and status == "success":
                norm = normalize_url(url)
                orig = url_to_original.get(norm)
                new_wc = len(str(result.markdown).split()) if result.markdown else 0
                if orig and new_wc > orig[1]:
                    improved += 1
                else:
                    unchanged += 1
            else:
                unchanged += 1

        return await run_job(job, on_result=on_result)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Re-crawling short pages..."),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Re-crawling", total=len(sites))

        # Wrap run_job to update progress
        orig_run = _run

        async def _run_with_progress() -> list[CrawlStats]:
            async def on_result(result, site: SiteConfig, depth_val: int, retries: int) -> None:
                nonlocal improved, unchanged
                url = getattr(result, "url", "") or site.url

                if not result.success:
                    unchanged += 1
                    return

                filepath, status = write_markdown(
                    url=url, result=result, output_dir=str(output_dir),
                    format=format, depth=depth_val, min_word_count=30,
                )

                if filepath and status == "success":
                    norm = normalize_url(url)
                    orig = url_to_original.get(norm)
                    new_wc = len(str(result.markdown).split()) if result.markdown else 0
                    if orig and new_wc > orig[1]:
                        improved += 1
                    else:
                        unchanged += 1
                else:
                    unchanged += 1

            async def on_site_done(stats: CrawlStats) -> None:
                progress.update(task, advance=1)

            return await run_job(job, on_result=on_result, on_site_done=on_site_done)

        all_stats = asyncio.run(_run_with_progress())

    duration = time.monotonic() - start_time
    console.print(f"\n[bold]Re-crawl complete:[/bold]")
    console.print(f"  [green]{improved} pages improved[/green]")
    console.print(f"  [dim]{unchanged} pages unchanged[/dim]")
    console.print(f"  [bold]Time:[/bold] {duration:.1f}s")


@app.command("reprocess")
def reprocess(
    output_dir: Path = typer.Argument(..., help="Output directory with crawl results (e.g. ./claude-kb)"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Only reprocess files from this domain (e.g. github.com)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Re-run writer cleanup on existing markdown files without re-crawling.

    Useful after improving writer.py post-processing (e.g. GitHub noise removal).
    """
    _setup_logging(verbose)
    from .writer import clean_markdown, build_frontmatter

    if not output_dir.exists():
        console.print(f"[red]Error:[/red] Directory not found: {output_dir}")
        raise typer.Exit(1)

    all_files = [f for f in output_dir.rglob("*.md") if f.is_file()]
    if domain:
        all_files = [f for f in all_files if f"/{domain}/" in str(f)]

    processed = 0
    changed = 0
    errors = 0

    for fp in all_files:
        meta = _parse_frontmatter(fp)
        source_url = meta.get("source_url", "")
        if not source_url:
            continue

        try:
            text = fp.read_text(encoding="utf-8")
        except Exception:
            errors += 1
            continue

        # Extract body (after frontmatter)
        if text.startswith("---"):
            end = text.find("---", 3)
            if end == -1:
                continue
            body = text[end + 3:].strip()
        else:
            body = text.strip()

        if not body:
            continue

        # Re-clean through updated writer
        cleaned = clean_markdown(body, url=source_url)
        new_frontmatter = build_frontmatter(source_url, cleaned, depth=meta.get("crawl_depth", 0))
        new_content = new_frontmatter + "\n" + cleaned + "\n"

        if new_content != text:
            fp.write_text(new_content, encoding="utf-8")
            changed += 1

        processed += 1

    scope = f" (domain: {domain})" if domain else ""
    console.print(f"[bold]Reprocessed{scope}:[/bold] {processed} files, {changed} updated, {errors} errors")


@app.command()
def init_config(
    output: Path = typer.Option("crawl_config.yaml", "--output", "-o", help="Output path for config file"),
) -> None:
    """Generate a sample YAML config file."""
    sample = """\
# crawl4ai-cli configuration
output_dir: ./output
delay: 1.5
concurrency: 3
pruning_threshold: 0.48
markdown_format: fit         # fit, raw, or citations
min_word_count: 20           # skip pages with fewer words
max_retries: 2               # retry on timeout/429/503
retry_delay: 3.0             # base delay between retries (doubles each attempt)

sites:
  - url: https://docs.example.com
    max_depth: 3
    max_pages: 200
    css_selector: main       # target main content area
    wait_until: networkidle  # wait for JS to finish
    page_timeout: 60         # seconds
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
"""
    output.write_text(sample, encoding="utf-8")
    console.print(f"[green]Config written to:[/green] {output}")


if __name__ == "__main__":
    app()
