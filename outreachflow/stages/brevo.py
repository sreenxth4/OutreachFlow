"""Stage 4 — Brevo: Send personalized cold emails.

Sends personalized outreach emails using the Brevo
transactional email API.

API Details:
    Base URL: https://api.brevo.com/v3
    Auth: api-key header
    Send: POST /smtp/email
    Health: GET /account
    Rate limit: 300 emails/day (free tier)
"""

import logging
import time
from typing import Dict, List

import requests
from rich.console import Console

from ..config import Settings
from ..models import Lead
from ..utils.retry import api_call_with_retry


logger = logging.getLogger("outreachflow")
console = Console(force_terminal=True)

BASE_URL = "https://api.brevo.com/v3"
SEND_ENDPOINT = "/smtp/email"
ACCOUNT_ENDPOINT = "/account"


def _get_headers(api_key: str) -> dict:
    """Build Brevo request headers."""
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": api_key,
    }


def health_check(settings: Settings) -> bool:
    """Verify Brevo API connectivity and authentication.

    Hits the account endpoint to validate the API key.
    """
    try:
        response = requests.get(
            f"{BASE_URL}{ACCOUNT_ENDPOINT}",
            headers=_get_headers(settings.brevo_api_key),
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            email = data.get("email", "unknown")
            logger.info(f"Brevo health check passed (account: {email})")
            return True
        else:
            logger.error(
                f"Brevo health check failed: "
                f"HTTP {response.status_code} — {response.text}"
            )
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Brevo health check failed: {e}")
        return False


def _build_email_html(first_name: str, company_name: str) -> str:
    """Generate personalized email HTML content.

    Args:
        first_name: Recipient's first name.
        company_name: Recipient's company name.

    Returns:
        HTML string for the email body.
    """
    return f"""<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #333; line-height: 1.6;">
<p>Hi {first_name},</p>

<p>Came across {company_name} while building OutreachFlow &mdash;
a pipeline that automates the entire outreach process from
finding lookalike companies to sending personalized emails.
Zero manual steps.</p>

<p>Thought it might save your team serious hours every week
given the scale {company_name} operates at.</p>

<p>Open to a quick 15-minute chat?</p>

<p>Sreenath<br/>
<a href="https://outreachflow.me" style="color: #2563eb;">outreachflow.me</a></p>
</body>
</html>"""


def _build_email_text(first_name: str, company_name: str) -> str:
    """Generate personalized plain-text email content."""
    return f"""Hi {first_name},

Came across {company_name} while building OutreachFlow —
a pipeline that automates the entire outreach process from
finding lookalike companies to sending personalized emails.
Zero manual steps.

Thought it might save your team serious hours every week
given the scale {company_name} operates at.

Open to a quick 15-minute chat?

Sreenath
outreachflow.me"""


def send_emails(
    leads: List[Lead],
    settings: Settings,
    dry_run: bool = False,
    mock: bool = False,
) -> Dict[str, int]:
    """Send personalized cold emails to verified leads.

    Args:
        leads: List of Lead objects with verified emails.
        settings: Pipeline settings.
        dry_run: If True, print what would be sent without sending.
        mock: If True, simulate sending without API calls.

    Returns:
        Dict with keys: 'sent', 'failed', 'skipped'.
    """
    results = {"sent": 0, "failed": 0, "skipped": 0}
    headers = _get_headers(settings.brevo_api_key)

    # Enforce max emails limit
    leads_to_send = leads[: settings.max_emails_to_send]

    for lead in leads_to_send:
        first_name = lead.contact.first_name
        company_name = lead.contact.company_name
        subject = f"quick idea for {company_name}"

        # --- Dry run mode ---
        if dry_run:
            console.print(
                f"  [bold cyan]\\[DRY RUN][/bold cyan] Would send to: "
                f"{lead.email} ({lead.contact.name}, {lead.contact.title}, "
                f"{company_name})"
            )
            logger.info(
                f"[DRY RUN] Would send to: {lead.email} "
                f"({lead.contact.name}, {lead.contact.title}, {company_name})"
            )
            results["sent"] += 1  # Count as success for reporting
            continue

        # --- Mock mode ---
        if mock:
            console.print(
                f"  [bold magenta]\\[MOCK][/bold magenta] Would send to: "
                f"{lead.email} ({lead.contact.name}, {lead.contact.title}, "
                f"{company_name})"
            )
            logger.info(
                f"[MOCK] Would send to: {lead.email} "
                f"({lead.contact.name}, {lead.contact.title}, {company_name})"
            )
            results["sent"] += 1
            continue

        # --- Live send ---
        payload = {
            "sender": {
                "name": settings.brevo_sender_name,
                "email": settings.brevo_sender_email,
            },
            "to": [
                {
                    "email": lead.email,
                    "name": lead.contact.name,
                }
            ],
            "subject": subject,
            "htmlContent": _build_email_html(first_name, company_name),
            "textContent": _build_email_text(first_name, company_name),
        }

        try:
            response = api_call_with_retry(
                "POST",
                f"{BASE_URL}{SEND_ENDPOINT}",
                headers=headers,
                json_data=payload,
                service_name="Brevo",
            )

            if response.status_code in (200, 201):
                message_id = response.json().get("messageId", "unknown")
                logger.info(
                    f"  Sent to {lead.email} (messageId: {message_id})"
                )
                results["sent"] += 1
            elif response.status_code == 401:
                logger.error("Brevo authentication failed")
                raise PermissionError("Brevo authentication failed")
            else:
                logger.error(
                    f"  Failed to send to {lead.email}: "
                    f"HTTP {response.status_code} — {response.text}"
                )
                results["failed"] += 1

        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"  Failed to send to {lead.email}: {e}")
            results["failed"] += 1

        # Small delay between sends
        time.sleep(0.5)

    logger.info(
        f"Brevo: {results['sent']} sent, {results['failed']} failed, "
        f"{results['skipped']} skipped"
    )
    return results
