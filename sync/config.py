"""
Configuration for Hevy -> Garmin sync.
All users are defined in a single JSON file (one or many entries); same flow for everyone.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Project root for default paths
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USERS_FILENAME = "users.json"


@dataclass
class HevyConfig:
    api_key: str
    base_url: str = "https://api.hevyapp.com"


@dataclass
class GarminConfig:
    email: str
    password: str
    # Optional: path to existing garth session (avoids re-auth)
    session_path: Optional[str] = None


@dataclass
class SyncConfig:
    hevy: HevyConfig
    garmin: GarminConfig
    # Optional label for logs (e.g. "alice"); used for default state file name when loading from users file
    user_id: Optional[str] = None
    # Sync last N days of Hevy workouts (we still only upload ones not yet in state)
    days_back: int = 7
    # Skip workouts already in synced state file (no duplicates on Garmin)
    skip_existing: bool = True
    # Path to JSON file that stores synced Hevy workout IDs (default: project root)
    state_file: Optional[Path] = None
    # Path to Hevy→Garmin exercise mapping JSON (default: project root)
    mapping_file: Optional[Path] = None
    # Path to JSON file where unmapped Hevy exercises are recorded (default: project root)
    unmapped_file: Optional[Path] = None


def load_config() -> list[SyncConfig]:
    """
    Load user configs from the users file (one or more users, same format).
    Path: HEVY_GARMIN_USERS_FILE env, or project root / users.json.
    Returns a list of SyncConfig so the caller can sync them one by one.
    """
    users_file = (os.environ.get("HEVY_GARMIN_USERS_FILE") or "").strip()
    if users_file:
        path = Path(users_file).resolve()
    else:
        path = _PROJECT_ROOT / DEFAULT_USERS_FILENAME
    if not path.exists():
        raise ValueError(
            f"Users file not found: {path}. "
            f"Copy users.json.example to users.json and add your user(s), "
            f"or set HEVY_GARMIN_USERS_FILE to the file path."
        )
    return _load_config_from_file(path)


def _load_config_from_file(path: Path) -> list[SyncConfig]:
    """Load users from JSON: { "users": [ { "id", "hevy_auth_token", "garmin_email", "garmin_password", ... }, ... ] }."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Could not load users file {path}: {e}") from e
    if not isinstance(data, dict) or "users" not in data:
        raise ValueError(f"Users file must contain a 'users' array: {path}")
    raw = data["users"]
    if not isinstance(raw, list):
        raise ValueError(f"'users' must be an array: {path}")
    configs: list[SyncConfig] = []
    for i, u in enumerate(raw):
        if not isinstance(u, dict):
            continue
        uid = (u.get("id") or str(i)).strip()
        if not uid:
            uid = str(i)
        hevy_api_key = (u.get("hevy_api_key") or "").strip()
        garmin_email = (u.get("garmin_email") or "").strip()
        garmin_password = (u.get("garmin_password") or "").strip()
        if not hevy_api_key or not garmin_email or not garmin_password:
            raise ValueError(f"User '{uid}' in {path}: hevy_api_key, garmin_email, garmin_password are required.")
        days = 7
        if "days_back" in u:
            try:
                days = max(1, min(365, int(u["days_back"])))
            except (TypeError, ValueError):
                pass
        session_path = (u.get("garth_session_path") or "").strip() or None
        state_file = (u.get("state_file") or "").strip()
        if not state_file:
            state_file = str(_PROJECT_ROOT / f"hevy_garmin_synced_{uid}.json")
        mapping_file = (u.get("mapping_file") or "").strip() or None
        unmapped_file = (u.get("unmapped_file") or "").strip() or None
        hevy_base_url = (u.get("hevy_base_url") or "https://api.hevyapp.com").strip()
        configs.append(
            SyncConfig(
                hevy=HevyConfig(api_key=hevy_api_key, base_url=hevy_base_url),
                garmin=GarminConfig(email=garmin_email, password=garmin_password, session_path=session_path),
                user_id=uid,
                days_back=days,
                skip_existing=True,
                state_file=Path(state_file),
                mapping_file=Path(mapping_file) if mapping_file else None,
                unmapped_file=Path(unmapped_file) if unmapped_file else None,
            )
        )
    if not configs:
        raise ValueError(f"No valid users in {path}")
    return configs
