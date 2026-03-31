from __future__ import annotations

import asyncio
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

from .config import CrawlJobConfig, SiteConfig, config_from_cli_args, load_config
from .engine import run_job, CrawlStats
from .manifest import ManifestCollector
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
