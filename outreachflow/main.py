#!/usr/bin/env python3
"""OutreachFlow — Automated Cold Outreach Pipeline.

One domain in → personalized emails out.

Usage:
    python -m outreachflow.main --domain stripe.com          # Normal run
    python -m outreachflow.main --domain stripe.com --dry-run # No emails sent
    python -m outreachflow.main --domain stripe.com --mock    # Use mock data

Author: Sreenath
Domain: outreachflow.me
"""

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional

# Force UTF-8 output on Windows to avoid cp1252 encoding errors with Rich
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from .config import Settings, load_settings
from .models import Company, Contact, Lead
from .utils.logger import setup_logger
from .utils.cleaner import clean_domain, deduplicate_companies, is_valid_domain
from .stages.ocean import find_lookalikes, health_check as ocean_health_check
from .stages.prospeo import find_contacts, health_check as prospeo_health_check
from .stages.brevo import send_emails, health_check as brevo_health_check


# ──────────────────────────────────────────────
# Rich console instance for beautiful output
# ──────────────────────────────────────────────
console = Console(force_terminal=True)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        prog="outreachflow",
        description="OutreachFlow - Automated Cold Outreach Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m outreachflow.main --domain stripe.com
  python -m outreachflow.main --domain stripe.com --dry-run
  python -m outreachflow.main --domain stripe.com --mock
        """,
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Seed company domain (e.g., stripe.com)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all stages but do NOT send emails",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock data instead of live API calls",
    )
    return parser.parse_args()


def validate_domain(domain: str) -> str:
    """Validate and clean the input domain.

    Args:
        domain: Raw domain input from CLI.

    Returns:
        Cleaned, validated domain string.

    Raises:
        SystemExit: If domain is invalid.
    """
    cleaned = clean_domain(domain)
    if not is_valid_domain(cleaned):
        console.print(f"\n[bold red]X Invalid domain:[/bold red] '{domain}'")
        console.print("   Please provide a valid domain (e.g., stripe.com)")
        sys.exit(1)
    return cleaned


def run_health_checks(settings: Settings) -> None:
    """Run health checks for all API services.

    Verifies connectivity to Ocean.io, Prospeo, and Brevo.
    Exits immediately if any health check fails.

    Args:
        settings: Pipeline settings with API keys.
    """
    console.print("\n[bold blue]>> Running Health Checks...[/bold blue]\n")

    checks = [
        ("Ocean.io", ocean_health_check),
        ("Prospeo", prospeo_health_check),
        ("Brevo", brevo_health_check),
    ]

    all_passed = True
    for name, check_fn in checks:
        console.print(f"  Checking {name} API...", end="  ")
        try:
            passed = check_fn(settings)
            if passed:
                console.print("[bold green]OK[/bold green]")
            else:
                console.print("[bold red]FAILED[/bold red]")
                all_passed = False
        except Exception as e:
            console.print(f"[bold red]FAILED ({e})[/bold red]")
            all_passed = False

    if not all_passed:
        console.print(
            "\n[bold red]X Health checks failed. Fix API configuration and retry.[/bold red]\n"
        )
        sys.exit(1)

    console.print("\n[bold green]All health checks passed![/bold green]\n")


def display_safety_checkpoint(
    leads: List[Lead],
    num_companies: int,
) -> bool:
    """Display safety checkpoint before sending emails.

    Shows a summary of all leads about to receive emails and
    prompts for user confirmation.

    Args:
        leads: List of verified leads to send to.
        num_companies: Number of companies found in Stage 1.

    Returns:
        True if user confirms, False otherwise.
    """
    console.print()

    # Build the lead table
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Company", style="green")
    table.add_column("Email", style="yellow")

    for i, lead in enumerate(leads, 1):
        table.add_row(
            str(i),
            lead.contact.name,
            lead.contact.title,
            lead.contact.company_name,
            lead.email,
        )

    # Print summary panel with actual counts
    console.print(Panel(
        f"[bold]Companies found     :[/bold] {num_companies}\n"
        f"[bold]Contacts + emails   :[/bold] {len(leads)}",
        title="[bold yellow]============ READY TO SEND ============[/bold yellow]",
        border_style="yellow",
    ))

    console.print(table)
    console.print()

    # Prompt for confirmation
    try:
        answer = console.input("[bold yellow]Proceed and send emails? \\[Y/N]: [/bold yellow]")
        return answer.strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]Aborted by user.[/yellow]")
        return False


def save_companies_csv(companies: List[Company], output_dir: str) -> None:
    """Save companies to CSV file.

    Args:
        companies: List of Company objects.
        output_dir: Directory to save the CSV file.
    """
    filepath = os.path.join(output_dir, "companies.csv")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "domain"])
        for company in companies:
            writer.writerow([company.name, company.domain])


def save_leads_csv(leads: List[Lead], output_dir: str) -> None:
    """Save verified leads to CSV file.

    Args:
        leads: List of Lead objects.
        output_dir: Directory to save the CSV file.
    """
    filepath = os.path.join(output_dir, "verified_emails.csv")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "title", "email", "company_name", "company_domain", "linkedin_url"])
        for lead in leads:
            writer.writerow([
                lead.contact.name,
                lead.contact.title,
                lead.email,
                lead.contact.company_name,
                lead.contact.company_domain,
                lead.contact.linkedin_url,
            ])


def save_run_report(
    run_id: str,
    domain: str,
    companies: List[Company],
    leads: List[Lead],
    enrichment_failed: int,
    send_results: Dict[str, int],
    stage_timings: Dict[str, float],
    output_dir: str,
    dry_run: bool,
    mock: bool,
    start_timestamp: str,
    max_contacts_per_company: int = 2,
) -> None:
    """Save comprehensive run report as JSON.

    Args:
        run_id: Correlation ID for this run.
        domain: Seed domain used.
        companies: Companies found in Stage 1.
        leads: Leads with verified emails from Stage 2.
        enrichment_failed: Number of contacts where enrichment failed.
        send_results: Email send results from Stage 3.
        stage_timings: Time taken per stage.
        output_dir: Directory to save the report.
        dry_run: Whether this was a dry run.
        mock: Whether this was a mock run.
        start_timestamp: ISO timestamp when pipeline started.
    """
    total_time = sum(stage_timings.values())

    # Calculate metrics safely
    total_contacts = len(leads) + enrichment_failed
    max_possible = len(companies) * max_contacts_per_company if companies else 1
    contact_rate = min(100.0, (total_contacts / max_possible * 100)) if max_possible > 0 else 0
    email_rate = (len(leads) / total_contacts * 100) if total_contacts > 0 else 0
    send_total = send_results["sent"] + send_results["failed"]
    send_rate = (send_results["sent"] / send_total * 100) if send_total > 0 else 0

    report = {
        "run_id": run_id,
        "seed_domain": domain,
        "timestamp_start": start_timestamp,
        "timestamp_end": datetime.now().isoformat(),
        "mode": "mock" if mock else ("dry_run" if dry_run else "live"),
        "counts": {
            "companies_found": len(companies),
            "contacts_enriched": len(leads),
            "enrichment_failed": enrichment_failed,
            "emails_sent": send_results["sent"],
            "emails_failed": send_results["failed"],
            "emails_skipped": send_results["skipped"],
        },
        "metrics": {
            "contact_discovery_rate": f"{contact_rate:.1f}%",
            "email_resolution_rate": f"{email_rate:.1f}%",
            "send_success_rate": f"{send_rate:.1f}%",
        },
        "stage_timings": {
            stage: f"{timing:.1f}s" for stage, timing in stage_timings.items()
        },
        "total_time": f"{total_time:.1f}s",
        "output_directory": output_dir,
    }

    filepath = os.path.join(output_dir, "run_report.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def display_run_summary(
    run_id: str,
    companies: List[Company],
    leads: List[Lead],
    enrichment_failed: int,
    send_results: Dict[str, int],
    stage_timings: Dict[str, float],
    output_dir: str,
    errors: List[str],
    max_contacts_per_company: int = 2,
) -> None:
    """Display the final run summary using rich formatting.

    Args:
        run_id: Correlation ID.
        companies: Companies found.
        leads: Leads with verified emails.
        enrichment_failed: Count of enrichment failures.
        send_results: Email send results.
        stage_timings: Per-stage timings.
        output_dir: Output directory path.
        errors: List of error messages from the run.
    """
    total_time = sum(stage_timings.values())

    # Calculate metrics safely
    total_contacts = len(leads) + enrichment_failed
    max_possible = len(companies) * max_contacts_per_company if companies else 1
    contact_rate = min(100.0, (total_contacts / max_possible * 100)) if max_possible > 0 else 0
    email_rate = (len(leads) / total_contacts * 100) if total_contacts > 0 else 0
    send_total = send_results["sent"] + send_results["failed"]
    send_rate = (send_results["sent"] / send_total * 100) if send_total > 0 else 0

    console.print()
    console.print(Panel(
        f"[bold]RUN ID:[/bold] {run_id}",
        title="[bold green]============ PIPELINE COMPLETE ============[/bold green]",
        border_style="green",
    ))

    # Results table
    results_table = Table(box=box.ROUNDED, show_header=False, border_style="green")
    results_table.add_column("Status", width=4)
    results_table.add_column("Metric", style="bold")
    results_table.add_column("Value", justify="right")

    results_table.add_row("[green]OK[/green]", "Companies sourced", str(len(companies)))
    results_table.add_row("[green]OK[/green]", "Contacts + emails", str(len(leads)))
    results_table.add_row("[green]OK[/green]", "Emails sent", str(send_results["sent"]))

    if enrichment_failed > 0:
        results_table.add_row(
            "[red]FAIL[/red]", "Failed",
            f"{enrichment_failed} (prospeo enrichment failed)"
        )
    if send_results["failed"] > 0:
        results_table.add_row("[red]FAIL[/red]", "Send failures", str(send_results["failed"]))
    if errors:
        for error in errors:
            results_table.add_row("[red]FAIL[/red]", "Error", error)

    console.print(results_table)

    # Stage timings table
    console.print("\n[bold]Stage timings:[/bold]")
    timing_table = Table(box=box.SIMPLE, show_header=False)
    timing_table.add_column("Stage", style="cyan", width=16)
    timing_table.add_column("Time", justify="right", style="yellow")

    for stage, timing in stage_timings.items():
        timing_table.add_row(f"  {stage}", f"{timing:.1f}s")
    timing_table.add_row("  [bold]Total[/bold]", f"[bold]{total_time:.1f}s[/bold]")

    console.print(timing_table)

    # Metrics
    console.print("\n[bold]Metrics:[/bold]")
    metrics_table = Table(box=box.SIMPLE, show_header=False)
    metrics_table.add_column("Metric", style="cyan", width=28)
    metrics_table.add_column("Value", justify="right", style="yellow")

    metrics_table.add_row("  Contact discovery rate", f"{contact_rate:.0f}%")
    metrics_table.add_row("  Email resolution rate", f"{email_rate:.0f}%")
    metrics_table.add_row("  Send success rate", f"{send_rate:.0f}%")

    console.print(metrics_table)

    console.print(f"\n[bold green]Output saved to:[/bold green] {output_dir}")
    console.print("[bold green]==========================================[/bold green]\n")


def main() -> None:
    """Main pipeline orchestrator.

    Parses arguments, runs health checks, executes all 3 stages
    sequentially, and produces output artifacts.
    """
    # ── Parse CLI arguments ──
    args = parse_args()
    domain = validate_domain(args.domain)
    dry_run: bool = args.dry_run
    mock: bool = args.mock

    # ── Generate correlation ID ──
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_timestamp = datetime.now().isoformat()

    # ── Setup output directory ──
    base_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, "output", f"run_{run_id}")
    os.makedirs(output_dir, exist_ok=True)

    # ── Setup logging ──
    logger = setup_logger(run_id)

    # ── Display banner ──
    mode_label = ""
    if mock:
        mode_label = " [bold magenta][MOCK MODE][/bold magenta]"
    elif dry_run:
        mode_label = " [bold cyan][DRY RUN MODE][/bold cyan]"

    console.print(Panel(
        f"[bold]Seed Domain:[/bold] {domain}\n"
        f"[bold]Run ID:[/bold]      {run_id}\n"
        f"[bold]Mode:[/bold]        {'Mock' if mock else ('Dry Run' if dry_run else 'Live')}",
        title=f"[bold blue]OutreachFlow{mode_label}[/bold blue]",
        border_style="blue",
    ))

    logger.info(f"Pipeline started | domain={domain} | run_id={run_id} | mock={mock} | dry_run={dry_run}")

    # ── Load settings ──
    if mock:
        # In mock mode, create dummy settings (no .env needed)
        settings = Settings(
            ocean_api_key="mock",
            prospeo_api_key="mock",
            brevo_api_key="mock",
        )
    else:
        settings = load_settings()

    # ── Health checks (skip in mock mode) ──
    if not mock:
        run_health_checks(settings)

    # ── Stage tracking ──
    stage_timings: Dict[str, float] = {}
    errors: List[str] = []
    enrichment_failed = 0

    # Build prefix for console output
    prefix = "[bold magenta]\\[MOCK][/bold magenta] " if mock else ""

    # ═══════════════════════════════════════════
    # STAGE 1 — Ocean.io: Find Lookalike Companies
    # ═══════════════════════════════════════════
    console.print(f"\n{prefix}[bold blue]━━━ Stage 1: Ocean.io — Finding Lookalike Companies ━━━[/bold blue]")

    t_start = time.time()
    try:
        companies = find_lookalikes(domain, settings, mock=mock)

        # Check if Ocean fell back to using the seed domain itself
        seed_is_target = (
            len(companies) == 1
            and companies[0].domain.lower() == domain.lower()
        )

        if seed_is_target:
            # Seed domain is the target — don't deduplicate it away
            console.print(
                f"  {prefix}[yellow]Ocean returned no lookalikes for {domain}; "
                f"using the seed domain as a fallback[/yellow]"
            )
        else:
            # Normal path: deduplicate + remove seed from lookalikes
            companies = deduplicate_companies(companies, domain)

        if not companies:
            console.print("[bold yellow]!! No companies found. Pipeline cannot continue.[/bold yellow]")
            logger.warning("Stage 1 produced no results. Exiting.")
            sys.exit(0)

        if not seed_is_target:
            console.print(f"  {prefix}[green]OK Found {len(companies)} lookalike companies[/green]")
        console.print(f"  {prefix}[green]OK {len(companies)} unique companies[/green]")
        for c in companies:
            console.print(f"     - {c.name} ({c.domain})")

    except Exception as e:
        console.print(f"  [bold red]X Stage 1 failed: {e}[/bold red]")
        logger.error(f"Stage 1 failed: {e}")
        sys.exit(1)

    stage_timings["Ocean"] = time.time() - t_start

    # ═══════════════════════════════════════════
    # STAGE 2 — Prospeo: Find Decision Makers + Enrich Emails
    # ═══════════════════════════════════════════
    console.print(f"\n{prefix}[bold blue]━━━ Stage 2: Prospeo — Finding Decision Makers + Emails ━━━[/bold blue]")

    t_start = time.time()
    try:
        leads, enrichment_failed = find_contacts(companies, settings, mock=mock)

        if not leads:
            console.print("[bold yellow]!! No leads found. Pipeline cannot continue.[/bold yellow]")
            logger.warning("Stage 2 produced no leads. Exiting.")
            sys.exit(0)

        console.print(f"  {prefix}[green]OK Found {len(leads)} contacts with verified emails[/green]")
        if enrichment_failed > 0:
            console.print(f"  {prefix}[yellow]⚠ {enrichment_failed} contacts failed enrichment[/yellow]")
        for lead in leads:
            console.print(f"     - {lead.contact.name} ({lead.contact.title}, {lead.contact.company_name}) → {lead.email}")

    except Exception as e:
        console.print(f"  [bold red]X Stage 2 failed: {e}[/bold red]")
        logger.error(f"Stage 2 failed: {e}")
        sys.exit(1)

    stage_timings["Prospeo"] = time.time() - t_start

    # ═══════════════════════════════════════════
    # SAFETY CHECKPOINT — Confirm before sending
    # ═══════════════════════════════════════════
    if not mock and not dry_run:
        if not display_safety_checkpoint(leads, len(companies)):
            console.print("\n[yellow]Aborted. No emails sent.[/yellow]")
            logger.info("User aborted at safety checkpoint")
            # Save partial results
            save_companies_csv(companies, output_dir)
            save_leads_csv(leads, output_dir)
            save_run_report(
                run_id, domain, companies, leads, enrichment_failed,
                {"sent": 0, "failed": 0, "skipped": 0},
                stage_timings, output_dir, dry_run, mock, start_timestamp,
                max_contacts_per_company=settings.max_contacts_per_company,
            )
            sys.exit(0)

    # ═══════════════════════════════════════════
    # STAGE 3 — Brevo: Send Personalized Emails
    # ═══════════════════════════════════════════
    if dry_run:
        console.print(f"\n[bold cyan]━━━ Stage 3: Brevo — Sending Emails [DRY RUN] ━━━[/bold cyan]")
    elif mock:
        console.print(f"\n[bold magenta]\\[MOCK][/bold magenta] [bold blue]━━━ Stage 3: Brevo — Sending Emails [MOCK] ━━━[/bold blue]")
    else:
        console.print(f"\n[bold blue]━━━ Stage 3: Brevo — Sending Emails ━━━[/bold blue]")

    t_start = time.time()
    try:
        send_results = send_emails(leads, settings, dry_run=dry_run, mock=mock)
        console.print(
            f"  {prefix}[green]OK Sent: {send_results['sent']} | "
            f"Failed: {send_results['failed']} | "
            f"Skipped: {send_results['skipped']}[/green]"
        )
    except Exception as e:
        console.print(f"  [bold red]X Stage 3 failed: {e}[/bold red]")
        logger.error(f"Stage 3 failed: {e}")
        errors.append(str(e))
        send_results = {"sent": 0, "failed": 0, "skipped": 0}

    stage_timings["Brevo"] = time.time() - t_start

    # ═══════════════════════════════════════════
    # SAVE OUTPUT ARTIFACTS
    # ═══════════════════════════════════════════
    save_companies_csv(companies, output_dir)
    save_leads_csv(leads, output_dir)
    save_run_report(
        run_id, domain, companies, leads, enrichment_failed,
        send_results, stage_timings, output_dir, dry_run, mock, start_timestamp,
        max_contacts_per_company=settings.max_contacts_per_company,
    )

    logger.info(f"Output artifacts saved to {output_dir}")

    # ═══════════════════════════════════════════
    # FINAL SUMMARY
    # ═══════════════════════════════════════════
    display_run_summary(
        run_id, companies, leads, enrichment_failed,
        send_results, stage_timings, output_dir, errors,
        max_contacts_per_company=settings.max_contacts_per_company,
    )

    logger.info(f"Pipeline completed | run_id={run_id}")


if __name__ == "__main__":
    main()
