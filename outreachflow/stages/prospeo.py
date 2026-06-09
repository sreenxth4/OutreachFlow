"""Stage 2 — Prospeo: Find decision-makers and enrich with verified emails.

Step A: Search for contacts at each company domain using /search-person.
Step B: Enrich each contact via /enrich-person to get verified work emails.
Both steps use the same PROSPEO_API_KEY.

API Details:
    Base URL: https://api.prospeo.io
    Auth: X-KEY header
    Search: POST /search-person
    Enrich: POST /enrich-person
    Health: GET /account-information (free, no credits consumed)
    Pagination: page-based, 25 results/page
    Rate limit: Free tier — 1/sec, 20/min, 50/day

Response Structures:
    /search-person returns results where each entry contains a nested
    "person" object with fields like:
      - person.full_name
      - person.current_job_title
      - person.linkedin_url

    /enrich-person returns:
      - error: bool
      - person.email (verified work email)
"""

import json
import logging
import os
import time
from typing import List, Tuple

import requests

from ..config import Settings
from ..models import Company, Contact, Lead
from ..utils.cleaner import is_decision_maker
from ..utils.retry import api_call_with_retry


logger = logging.getLogger("outreachflow")

BASE_URL = "https://api.prospeo.io"
SEARCH_ENDPOINT = "/search-person"
ENRICH_ENDPOINT = "/enrich-person"
ACCOUNT_ENDPOINT = "/account-information"


def _get_headers(api_key: str) -> dict:
    """Build Prospeo request headers."""
    return {
        "Content-Type": "application/json",
        "X-KEY": api_key,
    }


def health_check(settings: Settings) -> bool:
    """Verify Prospeo API connectivity and authentication.

    Uses the account-information endpoint (free, no credits consumed).
    """
    try:
        response = requests.get(
            f"{BASE_URL}{ACCOUNT_ENDPOINT}",
            headers=_get_headers(settings.prospeo_api_key),
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            if not data.get("error", False):
                credits = data.get("response", {}).get("remaining_credits", "unknown")
                logger.info(
                    f"Prospeo health check passed (credits remaining: {credits})"
                )
                return True
            else:
                logger.error(
                    f"Prospeo health check failed: "
                    f"{data.get('message', 'Unknown error')}"
                )
                return False
        else:
            logger.error(
                f"Prospeo health check failed: HTTP {response.status_code}"
            )
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Prospeo health check failed: {e}")
        return False


def _extract_person_field(result: dict, field: str) -> str:
    """Extract a field from Prospeo search result, handling nested/flat formats.

    Prospeo may return data as:
      - result["person"]["full_name"]  (nested)
      - result["full_name"]            (flat)

    Args:
        result: A single search result dict.
        field: Field name to extract (e.g., 'full_name').

    Returns:
        Field value as string, or empty string if not found.
    """
    # Try nested format first (documented structure)
    person = result.get("person", {})
    if isinstance(person, dict):
        value = person.get(field, "")
        if value:
            return str(value)

    # Fallback to flat format
    value = result.get(field, "")
    return str(value) if value else ""


def _enrich_contact(contact: Contact, headers: dict) -> str:
    """Enrich a single contact to get their verified email.

    Calls POST /enrich-person with the contact's LinkedIn URL.

    Args:
        contact: Contact object with linkedin_url.
        headers: Request headers with API key.

    Returns:
        Verified email address, or empty string if not found.
    """
    body = {
        "data": {
            "linkedin_url": contact.linkedin_url,
        }
    }

    try:
        response = api_call_with_retry(
            "POST",
            f"{BASE_URL}{ENRICH_ENDPOINT}",
            headers=headers,
            json_data=body,
            service_name="Prospeo (enrich)",
        )

        if response.status_code in (401, 403):
            logger.error("Prospeo enrichment authentication failed")
            raise PermissionError("Prospeo enrichment authentication failed")

        if response.status_code != 200:
            logger.warning(
                f"  Enrich HTTP {response.status_code} for {contact.name} — skipping"
            )
            return ""

        data = response.json()

        if data.get("error", False) is True:
            error_code = data.get("error_code", "")
            if error_code == "NO_MATCH":
                logger.debug(f"  Enrich: No match for {contact.name}")
            else:
                logger.warning(
                    f"  Enrich error for {contact.name}: "
                    f"{error_code or data.get('message', 'Unknown')}"
                )
            return ""

        # Parse email from response — try multiple paths for resilience
        # The API may return email as a string OR as a dict like:
        #   {"status": "VERIFIED", "email": "user@domain.com", ...}
        email = (
            data.get("person", {}).get("email")
            or data.get("email")
            or data.get("data", {}).get("email")
            or data.get("response", {}).get("email")
        )

        # Unwrap if email is a dict (Prospeo returns enrichment metadata)
        if isinstance(email, dict):
            email = email.get("email", "")

        if email and isinstance(email, str) and "@" in email:
            return email.strip()

        return ""

    except PermissionError:
        raise
    except Exception as e:
        logger.warning(f"  Enrich error for {contact.name}: {e}")
        return ""


def find_contacts(
    companies: List[Company],
    settings: Settings,
    mock: bool = False,
) -> Tuple[List[Lead], int]:
    """Find decision-maker contacts at each company and enrich with emails.

    Step A: Search for contacts at each company domain.
    Step B: Enrich each contact to get their verified email.

    Args:
        companies: List of Company objects from Stage 1.
        settings: Pipeline settings.
        mock: If True, load from mock/contacts.json instead.

    Returns:
        Tuple of (list of Lead objects, count of enrichment failures).
    """
    # --- Mock mode ---
    if mock:
        return _load_mock_data()

    # --- Live API ---
    contacts: List[Contact] = []
    headers = _get_headers(settings.prospeo_api_key)

    # ─── Step A: Find contacts by company domain ───
    rate_limit_waits = 0
    max_rate_limit_waits = 2  # Max 60s waits before giving up

    for company in companies:
        company_contacts_found = 0
        page = 1

        # If we've hit the daily quota, stop trying
        if rate_limit_waits >= max_rate_limit_waits:
            logger.warning(
                f"Prospeo: Skipping {company.domain} — daily rate limit likely exhausted"
            )
            continue

        logger.info(f"Prospeo: Searching for contacts at {company.domain}")

        while company_contacts_found < settings.max_contacts_per_company:
            # Build request — filter by company website and seniority
            # Correct filter format: company.websites.include (NOT person_search)
            body = {
                "page": page,
                "filters": {
                    "company": {
                        "websites": {"include": [company.domain]},
                    },
                    "person_seniority": {
                        "include": [
                            "C-Suite",
                            "Founder/Owner",
                            "Vice President",
                            "Director",
                        ],
                    },
                    "max_person_per_company": settings.max_contacts_per_company,
                },
            }

            try:
                response = api_call_with_retry(
                    "POST",
                    f"{BASE_URL}{SEARCH_ENDPOINT}",
                    headers=headers,
                    json_data=body,
                    service_name="Prospeo",
                )

                if response.status_code == 401:
                    logger.error("Prospeo authentication failed")
                    raise PermissionError("Prospeo authentication failed")

                if response.status_code == 429:
                    rate_limit_waits += 1
                    if rate_limit_waits >= max_rate_limit_waits:
                        logger.error(
                            "Prospeo: Daily rate limit likely exhausted — "
                            "stopping search"
                        )
                        break
                    logger.warning("Prospeo: Rate limited — waiting 60s")
                    time.sleep(60)
                    continue

                data = response.json()

                # Handle API-level errors
                error_code = data.get("error_code", "")
                if error_code == "NO_RESULTS":
                    logger.debug(
                        f"Prospeo: No results for {company.domain} (page {page})"
                    )
                    break

                if response.status_code == 404:
                    logger.warning(f"Prospeo: No results for {company.domain}")
                    break

                if response.status_code != 200:
                    logger.warning(
                        f"Prospeo: HTTP {response.status_code} for {company.domain}"
                        f" — {data.get('error_code', data.get('filter_error', ''))}"
                    )
                    break

                if data.get("error", False) is True:
                    logger.warning(
                        f"Prospeo error for {company.domain}: "
                        f"{error_code or data.get('message', 'Unknown')}"
                    )
                    break

                results = data.get("results", [])
                if not results:
                    break

                for person_result in results:
                    if not isinstance(person_result, dict):
                        continue

                    # Parse person and company data from result
                    person_data = person_result.get("person", person_result)
                    company_data = person_result.get("company", {})
                    if not isinstance(person_data, dict):
                        continue
                    if not isinstance(company_data, dict):
                        company_data = {}

                    # Extract title — try multiple field names
                    title = str(
                        person_data.get("job_title")
                        or person_data.get("title")
                        or person_data.get("current_job_title")
                        or ""
                    ).strip()

                    # Filter: decision-makers only
                    if not is_decision_maker(title):
                        continue

                    # Extract name — try full name, then first+last
                    first = str(person_data.get("first_name") or person_data.get("firstName") or "").strip()
                    last = str(person_data.get("last_name") or person_data.get("lastName") or "").strip()
                    name = str(
                        person_data.get("name")
                        or person_data.get("full_name")
                        or f"{first} {last}"
                    ).strip()

                    # Extract LinkedIn URL
                    linkedin = str(
                        person_data.get("linkedin_url")
                        or person_data.get("linkedin")
                        or person_data.get("linkedinUrl")
                        or ""
                    ).strip()

                    if not name or not linkedin:
                        continue

                    # Extract company name/domain from the result if available
                    result_company_name = str(
                        company_data.get("name") or company.name
                    )
                    result_company_domain = str(
                        company_data.get("website")
                        or company_data.get("domain")
                        or company.domain
                    )

                    contact = Contact(
                        name=name,
                        title=title,
                        linkedin_url=linkedin,
                        company_name=result_company_name,
                        company_domain=result_company_domain,
                    )
                    contacts.append(contact)
                    company_contacts_found += 1
                    logger.debug(
                        f"  Found: {name} ({title}) at {result_company_name}"
                    )

                    if company_contacts_found >= settings.max_contacts_per_company:
                        break

                # Check for more pages
                pagination = data.get("pagination", {})
                if isinstance(pagination, dict):
                    total_pages = int(pagination.get("total_page", page) or page)
                else:
                    total_pages = page
                if not results or page >= total_pages:
                    break  # No more pages

                page += 1

                # Respect rate limit — free tier: 1/sec, 20/min
                time.sleep(3.5)

            except PermissionError:
                raise
            except Exception as e:
                logger.warning(
                    f"Prospeo: Error searching {company.domain}: {e}"
                )
                break

        if company_contacts_found == 0:
            logger.warning(
                f"Prospeo: No decision-makers found at {company.domain}"
            )

        # Respect rate limit between companies
        time.sleep(3.5)

    # Deduplicate contacts by LinkedIn URL
    seen_urls: set[str] = set()
    unique_contacts: List[Contact] = []
    for contact in contacts:
        url_key = contact.linkedin_url.lower()
        if url_key not in seen_urls:
            seen_urls.add(url_key)
            unique_contacts.append(contact)

    logger.info(
        f"Prospeo: Found {len(unique_contacts)} decision-makers "
        f"across {len(companies)} companies"
    )

    # ─── Step B: Enrich each contact to get verified email ───
    leads: List[Lead] = []
    enrichment_failed = 0

    for contact in unique_contacts:
        # Validate LinkedIn URL before enriching
        if not contact.linkedin_url or "linkedin.com" not in contact.linkedin_url.lower():
            logger.warning(
                f"Prospeo: Skipping enrichment for {contact.name} — invalid LinkedIn URL"
            )
            enrichment_failed += 1
            continue

        logger.info(
            f"Prospeo: Enriching {contact.name} ({contact.company_name})"
        )

        email = _enrich_contact(contact, headers)

        if email:
            lead = Lead(contact=contact, email=email)
            leads.append(lead)
            logger.info(f"  Enriched: {contact.name} → {email}")
        else:
            logger.warning(f"  No email found for {contact.name}")
            enrichment_failed += 1

        # Respect rate limit
        time.sleep(1.1)

    # Deduplicate leads by email
    seen_emails: set[str] = set()
    unique_leads: List[Lead] = []
    for lead in leads:
        email_key = lead.email.lower()
        if email_key not in seen_emails:
            seen_emails.add(email_key)
            unique_leads.append(lead)

    logger.info(
        f"Prospeo: Enriched {len(unique_leads)} emails, "
        f"{enrichment_failed} failed"
    )
    return unique_leads, enrichment_failed


def _load_mock_data() -> Tuple[List[Lead], int]:
    """Load mock contact+email data from mock/contacts.json.

    Returns:
        Tuple of (list of Lead objects, 0 failures).
    """
    mock_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "mock",
        "contacts.json",
    )
    with open(mock_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    leads = []
    for item in data:
        contact = Contact(
            name=item["name"],
            title=item["title"],
            linkedin_url=item["linkedin_url"],
            company_name=item["company_name"],
            company_domain=item["company_domain"],
        )
        leads.append(Lead(contact=contact, email=item["email"]))

    logger.info(f"[MOCK] Loaded {len(leads)} leads from mock data")
    return leads, 0
