"""Data cleaning and validation utilities for OutreachFlow.

Handles domain cleaning, deduplication, title filtering,
and input validation across the pipeline.
"""

import re
from urllib.parse import urlparse
from typing import List

from ..models.company import Company


# Titles that qualify as decision-makers
DECISION_MAKER_TITLES: List[str] = [
    "ceo",
    "founder",
    "co-founder",
    "cofounder",
    "cto",
    "vp",
    "vice president",
    "director",
]


def clean_domain(raw: str) -> str:
    """Normalize a domain string.
    
    Strips protocol, www prefix, trailing slashes, and whitespace.
    
    Args:
        raw: Raw domain string (e.g., 'https://www.stripe.com/')
        
    Returns:
        Cleaned domain (e.g., 'stripe.com')
    """
    domain = raw.strip().lower()

    # Remove protocol
    if "://" in domain:
        domain = urlparse(domain).netloc or domain.split("://", 1)[1]

    # Remove www prefix
    if domain.startswith("www."):
        domain = domain[4:]

    # Remove trailing slashes and paths
    domain = domain.split("/")[0]

    return domain


def is_valid_domain(domain: str) -> bool:
    """Validate that a string looks like a real domain.
    
    Args:
        domain: Cleaned domain string.
        
    Returns:
        True if domain matches basic format (e.g., 'stripe.com').
    """
    pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$"
    return bool(re.match(pattern, domain))


def deduplicate_companies(companies: List[Company], seed_domain: str) -> List[Company]:
    """Remove duplicate companies and the seed domain.
    
    Args:
        companies: List of Company objects from Ocean.io.
        seed_domain: The original seed domain to exclude.
        
    Returns:
        Deduplicated list with seed domain removed.
    """
    seen: set[str] = set()
    cleaned_seed = clean_domain(seed_domain)
    result: List[Company] = []

    for company in companies:
        domain = clean_domain(company.domain)

        # Skip seed domain
        if domain == cleaned_seed:
            continue

        # Skip duplicates
        if domain in seen:
            continue

        seen.add(domain)
        result.append(Company(name=company.name, domain=domain))

    return result


def is_decision_maker(title: str) -> bool:
    """Check if a job title qualifies as a decision-maker.
    
    Args:
        title: Job title string (e.g., 'CEO', 'Co-Founder & CTO').
        
    Returns:
        True if title contains any decision-maker keyword.
    """
    if not title:
        return False
    title_lower = title.lower()
    return any(t in title_lower for t in DECISION_MAKER_TITLES)
