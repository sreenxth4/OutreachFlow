"""Stage 1 — Ocean.io: Find lookalike companies.

Given a seed company domain, uses Ocean.io's lookalike search
to find similar companies in the same market segment.

API Details:
    Base URL: https://api.ocean.io
    Auth: X-Api-Token header
    Search: POST /v3/search/companies  (with lookalikeDomains filter)
    Health: GET /v2/credits/balance
    Pagination: cursor-based via searchAfter token
    Rate limit: 60 req/min

Strategy:
    1. Try lookalikeDomains filter on /v3/search/companies
    2. If unavailable (free plan), fall back to category-based search
       using the seed domain's actual industry from Ocean.io
"""

import json
import logging
import os
from typing import List, Optional

import requests

from ..config import Settings
from ..models import Company
from ..utils.retry import api_call_with_retry


logger = logging.getLogger("outreachflow")

BASE_URL = "https://api.ocean.io"
SEARCH_COMPANIES_ENDPOINT = "/v3/search/companies"
CREDITS_ENDPOINT = "/v2/credits/balance"


def _get_headers(api_key: str) -> dict:
    """Build Ocean.io request headers."""
    return {
        "Content-Type": "application/json",
        "X-Api-Token": api_key,
    }


def _lookup_seed_industries(
    seed_domain: str,
    headers: dict,
) -> Optional[List[str]]:
    """Look up the seed domain's industries via Ocean.io.

    Searches for the seed domain in Ocean.io to extract its
    industry/category, so the fallback filter returns relevant
    companies instead of hardcoded ones.

    Args:
        seed_domain: Domain to look up (e.g., 'zomato.com').
        headers: Ocean.io request headers with auth token.

    Returns:
        List of industry strings for the seed company, or None if
        the lookup fails entirely (caller should skip category search
        and fall back to seed domain).
    """

    try:
        response = api_call_with_retry(
            "POST",
            f"{BASE_URL}{SEARCH_COMPANIES_ENDPOINT}",
            headers=headers,
            json_data={
                "size": 1,
                "fields": ["domain", "name", "industries", "industry", "category"],
                "companiesFilters": {
                    "domains": [seed_domain],
                },
            },
            service_name="Ocean.io (seed lookup)",
            max_retries=1,
        )

        if response.status_code == 200:
            data = response.json()
            results = data.get("companies", data.get("results", []))
            if results:
                company_data = results[0]
                if isinstance(company_data, dict):
                    company_data = company_data.get("company", company_data)

                industries: List[str] = []
                for field in ("industries", "industry", "category"):
                    value = company_data.get(field)
                    if value:
                        if isinstance(value, list):
                            industries.extend(
                                str(v) for v in value if v
                            )
                        elif isinstance(value, str):
                            industries.append(value)

                # Deduplicate while preserving order
                seen: set[str] = set()
                unique: List[str] = []
                for ind in industries:
                    if ind and ind.lower() not in seen:
                        seen.add(ind.lower())
                        unique.append(ind)

                if unique:
                    logger.info(
                        f"Ocean.io: Seed domain '{seed_domain}' industries: {unique}"
                    )
                    return unique

        logger.warning(
            f"Ocean.io: Could not resolve industries for '{seed_domain}' "
            f"(HTTP {response.status_code})"
        )
        return None

    except Exception as e:
        logger.warning(
            f"Ocean.io: Seed industry lookup failed for '{seed_domain}': {e}"
        )
        return None

    logger.warning(
        f"Ocean.io: All industry lookups failed for '{seed_domain}'"
    )
    return None


def health_check(settings: Settings) -> bool:
    """Verify Ocean.io API connectivity and authentication.

    Hits the credits/balance endpoint as a lightweight check.

    Args:
        settings: Pipeline settings with API key.

    Returns:
        True if API is reachable and authenticated.
    """
    try:
        response = requests.get(
            f"{BASE_URL}{CREDITS_ENDPOINT}",
            headers=_get_headers(settings.ocean_api_key),
            timeout=10,
        )
        if response.status_code == 200:
            logger.info("Ocean.io health check passed")
            return True
        else:
            logger.error(
                f"Ocean.io health check failed: HTTP {response.status_code} — {response.text}"
            )
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Ocean.io health check failed: {e}")
        return False


def _build_lookalike_body(
    seed_domain: str,
    page_size: int,
    search_after: Optional[str],
) -> dict:
    """Build request body for lookalike search via lookalikeDomains filter."""
    body: dict = {
        "size": page_size,
        "fields": ["name", "domain"],
        "companiesFilters": {
            "lookalikeDomains": [seed_domain],
            "excludeDomains": [seed_domain],
        },
    }
    if search_after:
        body["searchAfter"] = search_after
    return body


def _build_category_body(
    seed_industries: List[str],
    seed_domain: str,
    page_size: int,
    search_after: Optional[str],
) -> dict:
    """Build request body for category-based fallback search."""
    body: dict = {
        "size": page_size,
        "fields": ["name", "domain"],
        "companiesFilters": {
            "industries": {
                "industries": seed_industries,
            },
            "excludeDomains": [seed_domain],
        },
    }
    if search_after:
        body["searchAfter"] = search_after
    return body


def find_lookalikes(
    seed_domain: str,
    settings: Settings,
    mock: bool = False,
) -> List[Company]:
    """Find companies similar to the seed domain.

    Strategy:
        1. Try ``lookalikeDomains`` filter on ``/v3/search/companies``.
           This gives true lookalike results when the plan supports it.
        2. If the endpoint rejects the filter (free plan), fall back to
           category-based search using the seed domain's actual
           industry/category from Ocean.io.

    Args:
        seed_domain: Domain of the seed company (e.g., 'stripe.com').
        settings: Pipeline settings.
        mock: If True, load from mock/companies.json instead.

    Returns:
        List of Company objects (deduplicated, seed removed).
    """
    # --- Mock mode ---
    if mock:
        return _load_mock_data()

    # --- Live API ---
    companies: List[Company] = []
    search_after: Optional[str] = None
    headers = _get_headers(settings.ocean_api_key)

    use_lookalike = True       # Start with lookalike filter
    use_category_fallback = False
    seed_industries: Optional[List[str]] = None

    while len(companies) < settings.max_companies:
        page_size = min(50, settings.max_companies - len(companies) + 1)

        # Build the appropriate request body
        if use_lookalike:
            body = _build_lookalike_body(seed_domain, page_size, search_after)
            search_mode = "lookalikeDomains"
        else:
            # Resolve seed industries on first fallback iteration
            if seed_industries is None:
                seed_industries = _lookup_seed_industries(seed_domain, headers)

            # If we couldn't determine the seed's actual industries,
            # skip category search — generic categories return irrelevant
            # results. Fall through to seed-domain fallback instead.
            if seed_industries is None:
                logger.warning(
                    f"Ocean.io: Cannot determine industry for '{seed_domain}'; "
                    f"skipping category search"
                )
                break

            logger.info(
                f"Ocean.io: Using category filter for '{seed_domain}': "
                f"{seed_industries}"
            )
            body = _build_category_body(
                seed_industries, seed_domain, page_size, search_after
            )
            search_mode = "category"

        logger.info(
            f"Ocean.io: Searching [{search_mode}] via {SEARCH_COMPANIES_ENDPOINT} "
            f"({len(companies)} found so far)"
        )

        response = api_call_with_retry(
            "POST",
            f"{BASE_URL}{SEARCH_COMPANIES_ENDPOINT}",
            headers=headers,
            json_data=body,
            service_name="Ocean.io",
        )

        # --- Auth failure ---
        if response.status_code in (401, 403):
            logger.error(f"Ocean.io authentication failed: {response.text}")
            raise PermissionError(
                f"Ocean.io authentication failed (HTTP {response.status_code})"
            )

        # --- Lookalike filter rejected (free plan) → switch to category ---
        if response.status_code in (404, 422) and use_lookalike:
            logger.warning(
                "Lookalike search unavailable on free plan "
                "— showing similar companies by category"
            )
            use_lookalike = False
            use_category_fallback = True
            search_after = None
            continue  # Retry with category-based body

        # --- Other errors ---
        if response.status_code == 404:
            logger.warning(f"Ocean.io: HTTP 404 — {response.text}")
            break

        if response.status_code == 422:
            logger.error(f"Ocean.io: HTTP 422 — {response.text}")
            break

        if response.status_code != 200:
            logger.error(
                f"Ocean.io: Unexpected status {response.status_code} — {response.text}"
            )
            break

        # --- Parse results ---
        data = response.json()
        logger.debug(f"Ocean.io raw response keys: {list(data.keys())}")
        rows = data.get("companies", data.get("results", []))

        if not isinstance(rows, list):
            rows = []

        if not rows:
            # Check if the domain was rejected (e.g. "robots disallowed")
            missing = data.get("missingDomains", {})
            if missing and use_lookalike:
                reasons = ", ".join(
                    f"{d}: {r}" for d, r in missing.items()
                )
                logger.warning(
                    f"Ocean.io: Lookalike returned no results for "
                    f"'{seed_domain}' ({reasons}); falling back to category search"
                )
                use_lookalike = False
                use_category_fallback = True
                search_after = None
                continue  # Retry with category-based body
            elif not use_lookalike and use_category_fallback:
                # Category search also returned nothing — will fall back
                # to seed domain below
                logger.warning(
                    f"Ocean.io: Category search also returned no results. "
                    f"Response: {json.dumps(data)[:500]}"
                )
                break
            else:
                logger.warning(
                    f"Ocean.io: Empty results. Response: {json.dumps(data)[:500]}"
                )
                break

        for item in rows:
            if not isinstance(item, dict):
                continue
            # Ocean.io returns nested: {"company": {"domain": ..., "name": ...}}
            # Handle both nested and flat formats
            company_data = item.get("company", item)
            if not isinstance(company_data, dict):
                continue
            domain = str(company_data.get("domain") or company_data.get("website") or "")
            name = str(company_data.get("name") or domain)
            if domain:
                companies.append(Company(name=name, domain=domain))

        # Trim to limit
        if len(companies) >= settings.max_companies:
            companies = companies[: settings.max_companies]
            break

        # Pagination — cursor-based
        next_cursor = data.get("searchAfter")
        if not next_cursor or next_cursor == search_after:
            break  # No more pages
        search_after = str(next_cursor)

    # --- Seed domain fallback ---
    # If no lookalikes or category matches found, use the seed domain itself
    # as the target company (reach out to the seed company directly).
    if not companies:
        logger.warning(
            f"Ocean.io: No companies found via search; "
            f"using the seed domain '{seed_domain}' as a fallback"
        )
        companies = [Company(name=seed_domain, domain=seed_domain)]

    # --- Summary logging ---
    if use_category_fallback and len(companies) > 1:
        logger.info(
            f"Ocean.io: Lookalike search unavailable on free plan — "
            f"found {len(companies)} similar companies by category "
            f"(industries: {seed_industries})"
        )
    elif len(companies) == 1 and companies[0].domain == seed_domain:
        logger.info(
            f"Ocean.io: Using seed domain as target — 1 company"
        )
    else:
        logger.info(f"Ocean.io: Found {len(companies)} lookalike companies")

    return companies


def _load_mock_data() -> List[Company]:
    """Load mock company data from mock/companies.json."""
    mock_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "mock",
        "companies.json",
    )
    with open(mock_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    companies = [Company(name=item["name"], domain=item["domain"]) for item in data]
    logger.info(f"[MOCK] Loaded {len(companies)} companies from mock data")
    return companies
