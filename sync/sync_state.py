"""
Persistent state: which Hevy workout IDs have already been synced to Garmin.
Stored in a JSON file so each run skips already-synced workouts (no duplicates).
Uses atomic write (temp file + rename) to avoid corruption on crash or concurrent run.
"""
import json
import logging
from pathlib import Path
from typing import Set

log = logging.getLogger(__name__)

DEFAULT_STATE_FILENAME = "hevy_garmin_synced.json"


def _default_state_path() -> Path:
    """Default: same directory as this module, then up to project root."""
    return Path(__file__).resolve().parent.parent / DEFAULT_STATE_FILENAME


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path via a temp file and rename (atomic on same filesystem)."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def load_synced_ids(state_path: Path | None = None) -> Set[str]:
    """Load set of Hevy workout IDs that have already been synced."""
    path = (state_path or _default_state_path()).resolve()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("synced_workout_ids")
        if isinstance(ids, list):
            return set(str(x) for x in ids if x)
        return set()
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not load sync state from %s: %s. Starting fresh.", path, e)
        return set()


def save_synced_id(workout_id: str, state_path: Path | None = None) -> None:
    """Mark a Hevy workout ID as synced (append to state file). Atomic write."""
    if not (workout_id and str(workout_id).strip()):
        return
    path = (state_path or _default_state_path()).resolve()
    synced = load_synced_ids(path)
    synced.add(str(workout_id).strip())
    content = json.dumps({"synced_workout_ids": sorted(synced)}, indent=2)
    _atomic_write(path, content)
    log.debug("Saved sync state: %s synced so far", len(synced))
