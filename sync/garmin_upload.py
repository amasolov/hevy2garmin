"""
Garmin Connect upload via garth.
Uploads a .fit file (bytes or path) as a strength activity.

Authentication is separated from upload:
  1. Call garmin_authenticate() ONCE per sync run
  2. Call upload_fit() for each workout (no re-auth per upload)

For accounts with MFA, run garmin_login.py locally first to save a session.
"""
import io
import logging
import time
from pathlib import Path
from typing import Union

log = logging.getLogger(__name__)

try:
    import garth
except ImportError:
    garth = None

MAX_UPLOAD_RETRIES = 3
RETRY_BACKOFF_SEC = 5.0

_authenticated = False


def garmin_authenticate(
    email: str,
    password: str,
    session_path: Union[str, Path, None] = None,
) -> None:
    """
    Authenticate with Garmin Connect ONCE.  Call this before any uploads.

    Tries to resume a saved session first (no login needed).
    Falls back to garth.login() if no session exists.
    Saves the session after successful auth for future runs.

    Raises GarminAuthError on failure.
    """
    global _authenticated
    if garth is None:
        raise RuntimeError("garth is not installed. pip install garth")

    email = (email or "").strip()
    password = (password or "").strip()
    if not email or not password:
        raise GarminAuthError("Garmin email and password are required.")

    sp = str(session_path).strip() if session_path else None

    # Try resuming a saved session (avoids login + MFA entirely)
    if sp and Path(sp).exists():
        try:
            garth.resume(sp)
            garth.client.username
            _authenticated = True
            log.info("Garmin: resumed saved session from %s", sp)
            return
        except Exception as e:
            log.warning("Garmin: saved session invalid (%s), attempting fresh login...", e)

    # Fresh login — will fail if MFA is required and we're not interactive
    try:
        garth.login(email, password)
    except Exception as e:
        err_str = str(e)
        if "MFA" in err_str or "EOF" in err_str or "429" in err_str:
            raise GarminAuthError(
                f"Garmin login failed: {e}. "
                "Your account likely has MFA enabled or is rate-limited. "
                "Run 'python garmin_login.py <email> <password>' LOCALLY to create a session, "
                "then copy the session directory to the remote host and set 'garth_session_path' in users.json."
            ) from e
        raise GarminAuthError(f"Garmin login failed: {e}") from e

    _authenticated = True

    # Save session so future runs can just resume
    if sp:
        try:
            Path(sp).mkdir(parents=True, exist_ok=True)
            garth.save(sp)
            log.info("Garmin: session saved to %s", sp)
        except OSError as e:
            log.warning("Could not save Garmin session to %s: %s", sp, e)


def upload_fit(fit_bytes: Union[bytes, Path]) -> str:
    """
    Upload a FIT file to Garmin Connect.  Returns activity ID string.

    garmin_authenticate() must have been called first.
    Retries on transient upload errors (not auth errors).
    """
    if garth is None:
        raise RuntimeError("garth is not installed. pip install garth")
    if not _authenticated:
        raise GarminUploadError("Not authenticated. Call garmin_authenticate() first.")

    if isinstance(fit_bytes, Path):
        fit_bytes = fit_bytes.read_bytes()
    if not fit_bytes:
        raise ValueError("FIT data is empty.")

    last_exc = None
    for attempt in range(MAX_UPLOAD_RETRIES):
        try:
            f = io.BytesIO(fit_bytes)
            f.name = "workout.fit"
            result = garth.client.upload(f)
            if result is None:
                raise GarminUploadError("Garmin upload returned no result.")
            activity_id = result.get("activityId") if isinstance(result, dict) else str(result)
            return str(activity_id or result)
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "too many" in err_str:
                raise GarminRateLimitError(
                    f"Garmin rate-limited (429). Wait 15+ minutes before retrying."
                ) from e
            if "401" in err_str or "unauthorized" in err_str:
                raise GarminAuthError(
                    f"Garmin auth expired during upload: {e}. "
                    "Re-run garmin_login.py locally to refresh the session."
                ) from e
            last_exc = e
            if attempt < MAX_UPLOAD_RETRIES - 1:
                wait = RETRY_BACKOFF_SEC * (attempt + 1)
                log.warning(
                    "Upload attempt %d/%d failed: %s. Retrying in %.0fs.",
                    attempt + 1, MAX_UPLOAD_RETRIES, e, wait,
                )
                time.sleep(wait)
    raise GarminUploadError(
        f"Garmin upload failed after {MAX_UPLOAD_RETRIES} attempts: {last_exc}"
    ) from last_exc


class GarminUploadError(Exception):
    """Garmin upload failed (network or API error)."""


class GarminAuthError(Exception):
    """Garmin authentication failed (credentials, MFA, or rate-limit on login)."""


class GarminRateLimitError(Exception):
    """Garmin SSO rate-limited (429). Must wait before retrying."""
