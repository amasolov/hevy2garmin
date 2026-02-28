"""
Hevy API client using the official v1 API.
Auth: api-key header. Endpoint: /v1/workouts (paginated, newest first).

The Hevy API v1 uses snake_case in JSON responses (start_time, end_time,
exercise_template_id, weight_kg).  We normalize to camelCase at fetch time
so all downstream code uses a single convention.
"""
import logging
import re
import time
from datetime import datetime
from typing import Any, Iterator

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
RETRY_EXCEPTIONS = (requests.exceptions.Timeout, requests.exceptions.ConnectionError)
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 2.0

OFFICIAL_BASE_URL = "https://api.hevyapp.com"

# snake_case -> camelCase, with special rename for weight_kg -> weight
_SNAKE_RE = re.compile(r"_([a-z])")

_KEY_RENAMES = {
    "weight_kg": "weight",
}


def _to_camel(key: str) -> str:
    renamed = _KEY_RENAMES.get(key)
    if renamed:
        return renamed
    return _SNAKE_RE.sub(lambda m: m.group(1).upper(), key)


def _normalize_keys(obj: Any) -> Any:
    """Recursively convert dict keys from snake_case to camelCase."""
    if isinstance(obj, dict):
        return {_to_camel(k): _normalize_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_keys(item) for item in obj]
    return obj


def fetch_workouts(
    api_key: str,
    base_url: str = OFFICIAL_BASE_URL,
    page_size: int = 10,
    max_pages: int = 20,
) -> Iterator[dict[str, Any]]:
    """Yield workouts from Hevy official API v1 (newest first). Retries on transient failures."""
    url = f"{base_url.rstrip('/')}/v1/workouts"
    headers = {
        "accept": "application/json",
        "api-key": api_key.strip(),
    }
    page = 1
    _logged_sample = False
    while page <= max_pages:
        data = _fetch_page(url, headers, page, page_size)
        raw_workouts = data.get("workouts") or data.get("data") or []
        if isinstance(data, list):
            raw_workouts = data
        if not isinstance(raw_workouts, list):
            raise HevyAPIError("Hevy API response has no workout list.")
        if not raw_workouts:
            return

        for w in raw_workouts:
            if not isinstance(w, dict):
                continue
            normalized = _normalize_keys(w)
            if not _logged_sample:
                log.debug("Sample workout keys (raw): %s", list(w.keys())[:10])
                log.debug("Sample workout keys (normalized): %s", list(normalized.keys())[:10])
                _logged_sample = True
            yield normalized

        if len(raw_workouts) < page_size:
            return
        page += 1
        time.sleep(0.3)


def _fetch_page(url: str, headers: dict, page: int, page_size: int) -> dict:
    """Fetch a single page with retries."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(
                url,
                headers=headers,
                params={"page": page, "pageSize": page_size, "page_size": page_size},
                timeout=30,
            )
            if r.status_code == 401:
                raise HevyAuthError(
                    "Hevy auth failed (401). Check your hevy_api_key in users.json. "
                    "Get a key at https://www.hevyapp.com/settings (API Access)."
                )
            if r.status_code in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                _log_retry(r.status_code, attempt)
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
                continue
            r.raise_for_status()
            try:
                return r.json()
            except ValueError as e:
                raise HevyAPIError("Hevy API returned invalid JSON.") from e
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                raise HevyAuthError(
                    "Hevy auth failed (401). Check your hevy_api_key in users.json."
                ) from e
            raise
        except RETRY_EXCEPTIONS as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                log.warning(
                    "Hevy API error (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, e, RETRY_BACKOFF_SEC * (attempt + 1),
                )
                time.sleep(RETRY_BACKOFF_SEC * (attempt + 1))
    if last_exc is not None:
        raise HevyAPIError(f"Hevy API unreachable after {MAX_RETRIES} attempts.") from last_exc
    raise HevyAPIError("Hevy API returned no data.")


def _log_retry(status: int, attempt: int) -> None:
    log.warning(
        "Hevy API %s (attempt %d/%d), retrying in %.1fs",
        status, attempt + 1, MAX_RETRIES, RETRY_BACKOFF_SEC * (attempt + 1),
    )


def parse_start_time(workout: dict) -> datetime | None:
    """Parse startTime/start_time (ISO 8601) from workout dict. Returns None on failure."""
    raw = workout.get("startTime") or workout.get("start_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


class HevyAuthError(Exception):
    """Hevy authentication failed (invalid or missing api-key)."""


class HevyAPIError(Exception):
    """Hevy API returned an error or invalid response."""
