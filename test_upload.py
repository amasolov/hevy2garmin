#!/usr/bin/env python3
"""
Test script: build a FIT file from a small hardcoded workout and upload to Garmin.
Run locally with: python test_upload.py

Uses the local garth session (garth_session_alexey/) for auth.
Saves the FIT file locally as test_workout.fit for inspection.
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync.fit_builder import (
    workout_to_fit, _resolve_exercise,
    EXERCISE_CATEGORY, EXERCISE_NAME,
)
from sync.exercise_mapping import load_mapping, lookup, ensure_all_mapped

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# A realistic Hevy workout with a few exercises
TEST_WORKOUT = {
    "id": "test-001",
    "title": "Test Workout",
    "startTime": "2026-02-28T10:00:00+00:00",
    "endTime": "2026-02-28T10:30:00+00:00",
    "exercises": [
        {
            "index": 0,
            "name": "Squat (Dumbbell)",
            "exerciseTemplateId": "DCFF3E9F",
            "sets": [
                {"index": 0, "type": "normal", "weight": 15, "reps": 10, "duration": None},
                {"index": 1, "type": "normal", "weight": 20, "reps": 8, "duration": None},
                {"index": 2, "type": "normal", "weight": 20, "reps": 8, "duration": None},
            ],
        },
        {
            "index": 1,
            "name": "Deadlift (Dumbbell)",
            "exerciseTemplateId": "5F4E6DD3",
            "sets": [
                {"index": 0, "type": "normal", "weight": 10, "reps": 10, "duration": None},
                {"index": 1, "type": "normal", "weight": 15, "reps": 8, "duration": None},
            ],
        },
        {
            "index": 2,
            "name": "Lunge (Dumbbell)",
            "exerciseTemplateId": "B537D09F",
            "sets": [
                {"index": 0, "type": "normal", "weight": 8, "reps": 8, "duration": None},
                {"index": 1, "type": "normal", "weight": 10, "reps": 8, "duration": None},
            ],
        },
    ],
}

MAPPING_PATH = Path(__file__).resolve().parent / "exercise_mapping.json"
SESSION_DIR = Path(__file__).resolve().parent / "garth_session_alexey"


def debug_mapping():
    """Check exercise mapping is working."""
    # Ensure mapping file exists (copy from example if needed)
    example = MAPPING_PATH.with_suffix(".json.example")
    if not MAPPING_PATH.exists() and example.exists():
        log.info("Copying %s -> %s", example.name, MAPPING_PATH.name)
        MAPPING_PATH.write_text(example.read_text())

    mapping = load_mapping(MAPPING_PATH, use_cache=False)
    log.info("Loaded %d mapping entries", len(mapping))

    print("\n=== Exercise Mapping Debug ===")
    for ex in TEST_WORKOUT["exercises"]:
        name = ex["name"]
        tid = ex.get("exerciseTemplateId")
        result = lookup(name, tid, MAPPING_PATH, mapping=mapping)
        cat_id, name_id = _resolve_exercise(name, tid, MAPPING_PATH, mapping)

        print(f"\n  Hevy: '{name}' (template: {tid})")
        print(f"  Mapping lookup: {result}")
        print(f"  FIT IDs: category={cat_id}, exercise_name={name_id}")

        if result:
            cat_str, name_str = result
            cat_key = cat_str.strip().lower().replace(" ", "_")
            name_key = name_str.strip().lower()
            print(f"  Category key '{cat_key}' in EXERCISE_CATEGORY: {cat_key in EXERCISE_CATEGORY}")
            if cat_key in EXERCISE_NAME:
                print(f"  Name key '{name_key}' in EXERCISE_NAME['{cat_key}']: {name_key in EXERCISE_NAME[cat_key]}")
                if name_key not in EXERCISE_NAME[cat_key]:
                    print(f"  Available names: {list(EXERCISE_NAME[cat_key].keys())}")
        else:
            print("  ** UNMAPPED — will show as 'Choose an Exercise' on Garmin **")
    print()
    return mapping


def build_and_save(mapping):
    """Build FIT file and save locally."""
    fit_bytes = workout_to_fit(TEST_WORKOUT, mapping_path=MAPPING_PATH, mapping=mapping)
    out_path = Path(__file__).resolve().parent / "test_workout.fit"
    out_path.write_bytes(fit_bytes)
    print(f"=== FIT file saved: {out_path} ({len(fit_bytes)} bytes) ===\n")
    return fit_bytes


def upload(fit_bytes):
    """Upload to Garmin using local session."""
    import garth

    if not SESSION_DIR.exists():
        print(f"ERROR: No garth session at {SESSION_DIR}")
        print("Run: python garmin_login.py <email> <password> ./garth_session_alexey")
        sys.exit(1)

    garth.resume(str(SESSION_DIR))
    print(f"Garmin session resumed from {SESSION_DIR}")
    print(f"Logged in as: {garth.client.username}")

    import io
    f = io.BytesIO(fit_bytes)
    f.name = "test_workout.fit"
    result = garth.client.upload(f)
    print(f"\n=== Upload Result ===")
    print(json.dumps(result, indent=2))

    detail = result.get("detailedImportResult", {})
    upload_id = detail.get("uploadId")
    successes = detail.get("successes", [])
    failures = detail.get("failures", [])
    print(f"\nUpload ID: {upload_id}")
    print(f"Successes: {len(successes)}")
    print(f"Failures: {len(failures)}")
    for fail in failures:
        print(f"  - {fail}")

    return result


def main():
    print("=" * 60)
    print("  Hevy → Garmin FIT Test Upload")
    print("=" * 60)

    mapping = debug_mapping()
    fit_bytes = build_and_save(mapping)

    if "--no-upload" in sys.argv:
        print("Skipping upload (--no-upload flag)")
        return

    upload(fit_bytes)
    print("\nDone! Check Garmin Connect for 'Test Workout' activity.")
    print("Delete it when done testing.")


if __name__ == "__main__":
    main()
