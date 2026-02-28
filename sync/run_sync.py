"""
Main sync entrypoint: fetch Hevy workouts -> build FIT -> upload to Garmin.
Supports one or multiple users (users.json); syncs one by one.
Auto-updates the exercise mapping file with new exercises on every run.
"""
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

from sync.config import load_config, SyncConfig
from sync.exercise_mapping import ensure_all_mapped, load_mapping, lookup, record_unmapped
from sync.fit_builder import workout_to_fit
from sync.garmin_upload import (
    garmin_authenticate, upload_fit,
    GarminUploadError, GarminAuthError, GarminRateLimitError,
)
from sync.hevy_client import HevyAPIError, HevyAuthError, fetch_workouts, parse_start_time
from sync.sync_state import load_synced_ids, save_synced_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def run_sync(cfg: SyncConfig | None = None) -> None:
    """
    Run sync for one or more users. If cfg is given, sync only that user.
    Otherwise load users.json and sync each user one by one.
    """
    if cfg is not None:
        configs = [cfg]
    else:
        try:
            configs = load_config()
        except ValueError as e:
            log.error("%s", e)
            sys.exit(1)
        if not configs:
            log.error("No users to sync. Check your users file has a 'users' array with at least one entry.")
            sys.exit(1)

    failed = 0
    for c in configs:
        label = c.user_id or c.garmin.email or "user"
        try:
            run_sync_for_user(c)
        except HevyAuthError as e:
            log.error("[%s] %s", label, e)
            failed += 1
        except HevyAPIError as e:
            log.exception("[%s] Hevy API failed: %s", label, e)
            failed += 1
        except GarminAuthError as e:
            log.error("[%s] %s", label, e)
            failed += 1
        except GarminRateLimitError as e:
            log.error("[%s] %s", label, e)
            failed += 1
        except Exception as e:
            log.exception("[%s] Sync failed: %s", label, e)
            failed += 1

    if failed:
        sys.exit(1)


def run_sync_for_user(cfg: SyncConfig) -> None:
    """Sync a single user: fetch Hevy workouts, build FIT, upload to Garmin."""
    if not cfg.hevy.api_key or not cfg.garmin.email or not cfg.garmin.password:
        raise ValueError("Missing credentials: hevy_api_key, garmin_email, garmin_password")

    label = cfg.user_id or cfg.garmin.email
    log.info("Syncing user: %s", label)

    synced_ids = load_synced_ids(cfg.state_file) if cfg.skip_existing else set()
    if synced_ids:
        log.info("[%s] Skipping %d already-synced workouts", label, len(synced_ids))

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.days_back)

    # --- Phase 1: fetch all recent workouts into memory ---
    log.info("[%s] Fetching workouts from Hevy...", label)
    workouts: list[dict] = []
    try:
        for workout in fetch_workouts(api_key=cfg.hevy.api_key, base_url=cfg.hevy.base_url):
            if not isinstance(workout, dict):
                continue
            start_dt = parse_start_time(workout)
            if start_dt is not None and start_dt < cutoff:
                break
            workouts.append(workout)
    except (HevyAuthError, HevyAPIError):
        raise

    log.info("[%s] Fetched %d workout(s) from the last %d days", label, len(workouts), cfg.days_back)
    if not workouts:
        log.info("[%s] Nothing to sync.", label)
        return

    # --- Phase 2: auto-map new exercises and update mapping file ---
    mapping = ensure_all_mapped(workouts, mapping_path=cfg.mapping_file)

    for workout in workouts:
        _record_unmapped_exercises(workout, cfg, mapping)

    # Filter to only workouts that need uploading
    to_upload = []
    skipped = 0
    for workout in workouts:
        workout_id = (workout.get("id") or "").strip()
        if cfg.skip_existing and workout_id and workout_id in synced_ids:
            skipped += 1
            continue
        to_upload.append(workout)

    if not to_upload:
        log.info("[%s] All %d workout(s) already synced.", label, skipped)
        return

    # --- Phase 3: authenticate with Garmin ONCE ---
    log.info("[%s] Authenticating with Garmin Connect...", label)
    garmin_authenticate(cfg.garmin.email, cfg.garmin.password, cfg.garmin.session_path)
    log.info("[%s] Garmin authentication OK", label)

    # --- Phase 4: build FIT + upload for each new workout ---
    processed = 0
    upload_errors = 0

    for workout in to_upload:
        workout_id = (workout.get("id") or "").strip()
        name = workout.get("title") or workout_id
        start_str = workout.get("startTime") or ""
        log.info("[%s] Processing: %s (%s)", label, name, start_str)

        try:
            fit_bytes = workout_to_fit(
                workout,
                mapping_path=cfg.mapping_file,
                mapping=mapping,
            )
            log.info("[%s] Built FIT file (%d bytes) for: %s", label, len(fit_bytes), name)

            activity_id = upload_fit(fit_bytes)
            log.info("[%s] Uploaded to Garmin (activity %s): %s", label, activity_id, name)

            if workout_id:
                save_synced_id(workout_id, cfg.state_file)
            processed += 1

        except GarminRateLimitError as e:
            log.error("[%s] %s — stopping uploads for this user.", label, e)
            upload_errors += len(to_upload) - processed
            break
        except GarminAuthError as e:
            log.error("[%s] %s — stopping uploads for this user.", label, e)
            upload_errors += len(to_upload) - processed
            break
        except GarminUploadError as e:
            log.error("[%s] Upload failed for '%s': %s", label, name, e)
            upload_errors += 1
        except Exception as e:
            log.exception("[%s] Failed to process '%s': %s", label, name, e)
            upload_errors += 1

    log.info(
        "[%s] Done: %d uploaded, %d skipped, %d errors",
        label, processed, skipped, upload_errors,
    )


def _record_unmapped_exercises(
    workout: dict,
    cfg: SyncConfig,
    mapping: dict | None = None,
) -> None:
    """Record exercises that couldn't be auto-mapped at all (for manual review)."""
    exercises = workout.get("exercises")
    if not isinstance(exercises, list):
        return
    for ex in exercises:
        if not isinstance(ex, dict):
            continue
        try:
            title = (ex.get("name") or ex.get("title") or "").strip()
            template_id = ex.get("exerciseTemplateId") or ex.get("exercise_template_id")
            if template_id is not None:
                template_id = str(template_id).strip() or None
            if lookup(title, template_id, cfg.mapping_file, mapping=mapping) is None:
                record_unmapped(
                    title,
                    template_id,
                    None,
                    cfg.unmapped_file,
                    cfg.mapping_file,
                )
        except Exception as e:
            log.debug("Skip recording unmapped exercise %s: %s", ex.get("name"), e)


if __name__ == "__main__":
    run_sync()
