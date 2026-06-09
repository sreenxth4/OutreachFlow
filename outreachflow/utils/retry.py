"""Shared HTTP retry logic for all API stages.

Provides a single, consistent retry-with-backoff function
used across Ocean.io, Prospeo, and Brevo stages.
Avoids code duplication and ensures uniform retry behavior.
"""

import logging
import time
from typing import Optional

import requests


logger = logging.getLogger("outreachflow")


def api_call_with_retry(
    method: str,
    url: str,
    headers: dict,
    json_data: Optional[dict] = None,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    timeout: int = 30,
    service_name: str = "API",
) -> requests.Response:
    """Make an HTTP request with exponential backoff on rate limits and errors.

    Retries on:
      - 429 (rate limited) — wait and retry
      - Timeout — retry once, then raise
      - Connection errors — retry with backoff

    Args:
        method: HTTP method ('GET' or 'POST').
        url: Full URL to call.
        headers: Request headers.
        json_data: Optional JSON body for POST requests.
        max_retries: Maximum retry attempts on 429/timeout.
        backoff_base: Base wait time in seconds (doubles each retry).
        timeout: Request timeout in seconds.
        service_name: Human-readable service name for log messages.

    Returns:
        Response object from successful request.

    Raises:
        requests.exceptions.RequestException: After all retries exhausted.
    """
    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.request(
                method, url, headers=headers, json=json_data, timeout=timeout
            )

            # Rate limited — wait and retry
            if response.status_code == 429:
                if attempt < max_retries:
                    wait_time = backoff_base * (2 ** attempt)
                    logger.warning(
                        f"{service_name} rate limited (429). "
                        f"Waiting {wait_time}s (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"{service_name}: Rate limit retries exhausted")
                    return response  # Return the 429 response for caller to handle

            return response

        except requests.exceptions.Timeout as e:
            last_exception = e
            if attempt < max_retries:
                wait_time = backoff_base * (2 ** attempt)
                logger.warning(
                    f"{service_name} timeout. Retrying in {wait_time}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait_time)
                continue
            raise

        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt < max_retries:
                wait_time = backoff_base * (2 ** attempt)
                logger.warning(
                    f"{service_name} request failed: {e}. Retrying in {wait_time}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait_time)
                continue
            raise

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception
    raise requests.exceptions.RetryError(f"{service_name}: Max retries exceeded")
