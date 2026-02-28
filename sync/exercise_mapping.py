"""
Hevy → Garmin exercise mapping.

- Reads a JSON mapping file (hevy title or template_id → Garmin category + name).
- Auto-maps unknown exercises using keyword heuristics on the Hevy title.
- Persists newly auto-mapped exercises back to the mapping file every run.
- Uses atomic writes and caches the mapping per process to avoid repeated disk reads.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MAPPING_FILENAME = "exercise_mapping.json"
DEFAULT_UNMAPPED_FILENAME = "unmapped_exercises.json"

_mapping_cache: dict[tuple[Path | None, float], dict[str, dict[str, str]]] = {}
_mapping_cache_key: tuple[Path | None, float] | None = None


def _normalize_title(title: str) -> str:
    """Lowercase, collapse spaces, remove punctuation for lookup."""
    if not title:
        return ""
    s = re.sub(r"[^\w\s]", " ", title.lower())
    return " ".join(s.split())


def _default_mapping_path() -> Path:
    return Path(__file__).resolve().parent.parent / DEFAULT_MAPPING_FILENAME


def _default_unmapped_path() -> Path:
    return Path(__file__).resolve().parent.parent / DEFAULT_UNMAPPED_FILENAME


def _atomic_write(path: Path, content: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Loading / lookup
# ---------------------------------------------------------------------------

def load_mapping(path: Path | None = None, use_cache: bool = True) -> dict[str, dict[str, str]]:
    """
    Load mapping: key (normalized title or template_id) -> { "category": "...", "name": "..." }.
    Returns dict; missing file or invalid JSON => empty dict.
    """
    global _mapping_cache_key
    p = (path or _default_mapping_path()).resolve()
    if use_cache and p.exists():
        try:
            mtime = p.stat().st_mtime
            key = (path, mtime)
            if _mapping_cache_key == key and key in _mapping_cache:
                return _mapping_cache[key]
        except OSError:
            pass
    if not p.exists():
        if use_cache:
            _mapping_cache_key = (path, 0.0)
            _mapping_cache[_mapping_cache_key] = {}
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not load exercise mapping from %s: %s", p, e)
        return {}
    out: dict[str, dict[str, str]] = {}
    if isinstance(data, dict) and "exercises" in data:
        exercises = data["exercises"]
        if not isinstance(exercises, list):
            exercises = []
        for entry in exercises:
            if not isinstance(entry, dict):
                continue
            title = entry.get("hevy_title") or ""
            template_id = entry.get("hevy_template_id") or ""
            cat = entry.get("category") or ""
            name = entry.get("name") or ""
            if title:
                out[_normalize_title(title)] = {"category": cat, "name": name}
            if template_id:
                out[template_id] = {"category": cat, "name": name}
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict) and "category" in v:
                out[str(k)] = {"category": v.get("category", ""), "name": v.get("name", "")}
    if use_cache and p.exists():
        try:
            key = (path, p.stat().st_mtime)
            _mapping_cache_key = key
            _mapping_cache[key] = out
        except OSError:
            pass
    return out


def lookup(
    hevy_title: str,
    hevy_template_id: str | None,
    mapping_path: Path | None = None,
    mapping: dict[str, dict[str, str]] | None = None,
) -> tuple[str, str] | None:
    """
    Look up Garmin (category, name) for a Hevy exercise.
    Tries normalized title first, then template_id. Returns None if not found.
    """
    if mapping is None:
        mapping = load_mapping(mapping_path)
    key_title = _normalize_title(hevy_title or "")
    if key_title and key_title in mapping:
        m = mapping[key_title]
        if m.get("category") and m.get("name"):
            return (m["category"], m["name"])
    tid = (hevy_template_id or "").strip()
    if tid and tid in mapping:
        m = mapping[tid]
        if m.get("category") and m.get("name"):
            return (m["category"], m["name"])
    return None


# ---------------------------------------------------------------------------
# Auto-mapping: keyword heuristics to guess FIT category + exercise name
# ---------------------------------------------------------------------------

# Ordered from most-specific to least-specific so first match wins.
_CATEGORY_PATTERNS: list[tuple[str, str]] = [
    # Multi-word specifics first
    ("jump squat", "plyo"),
    ("box jump", "plyo"),
    ("man maker", "total_body"),
    ("burpee", "total_body"),
    ("bench press", "bench_press"),
    ("chest press", "bench_press"),
    ("floor press", "bench_press"),
    ("incline press", "bench_press"),
    ("decline press", "bench_press"),
    ("shoulder press", "shoulder_press"),
    ("overhead press", "shoulder_press"),
    ("military press", "shoulder_press"),
    ("arnold press", "shoulder_press"),
    ("push press", "shoulder_press"),
    ("lat pulldown", "pull_up"),
    ("pull up", "pull_up"),
    ("pullup", "pull_up"),
    ("chin up", "pull_up"),
    ("chinup", "pull_up"),
    ("push up", "push_up"),
    ("pushup", "push_up"),
    ("face pull", "row"),
    ("cable row", "row"),
    ("seated row", "row"),
    ("bent over row", "row"),
    ("upright row", "shrug"),
    ("hip thrust", "hip_raise"),
    ("glute bridge", "hip_raise"),
    ("kettlebell swing", "hip_swing"),
    ("calf raise", "calf_raise"),
    ("calf press", "calf_raise"),
    ("lateral raise", "lateral_raise"),
    ("side raise", "lateral_raise"),
    ("front raise", "lateral_raise"),
    ("leg raise", "leg_raise"),
    ("hanging leg", "leg_raise"),
    ("hanging knee", "leg_raise"),
    ("leg curl", "leg_curl"),
    ("hamstring curl", "leg_curl"),
    ("good morning", "leg_curl"),
    ("leg extension", "crunch"),
    ("leg press", "squat"),
    ("cable crossover", "flye"),
    ("chest fly", "flye"),
    ("reverse fly", "flye"),
    ("skull crusher", "triceps_extension"),
    ("tricep extension", "triceps_extension"),
    ("triceps extension", "triceps_extension"),
    ("tricep pushdown", "triceps_extension"),
    ("triceps pushdown", "triceps_extension"),
    ("tricep kickback", "triceps_extension"),
    ("rope pushdown", "triceps_extension"),
    ("overhead extension", "triceps_extension"),
    ("russian twist", "core"),
    ("ab wheel", "core"),
    ("mountain climber", "plank"),
    ("bicycle crunch", "crunch"),
    ("reverse crunch", "crunch"),
    ("curtsy lunge", "lunge"),
    ("reverse lunge", "lunge"),
    ("lateral lunge", "lunge"),
    ("walking lunge", "lunge"),
    ("split squat", "lunge"),
    ("bulgarian", "lunge"),
    ("step up", "squat"),
    ("romanian deadlift", "deadlift"),
    ("rdl", "deadlift"),
    ("sumo deadlift", "deadlift"),
    ("stiff leg", "deadlift"),
    ("straight leg deadlift", "deadlift"),
    ("rack pull", "deadlift"),
    # Single keywords (broader, checked last)
    ("deadlift", "deadlift"),
    ("lunge", "lunge"),
    ("squat", "squat"),
    ("goblet", "squat"),
    ("pistol", "squat"),
    ("thruster", "squat"),
    ("row", "row"),
    ("shrug", "shrug"),
    ("crunch", "crunch"),
    ("sit up", "sit_up"),
    ("situp", "sit_up"),
    ("plank", "plank"),
    ("curl", "curl"),
    ("bicep", "curl"),
    ("dip", "triceps_extension"),
    ("fly", "flye"),
    ("flye", "flye"),
    ("warm up", "warm_up"),
    ("warmup", "warm_up"),
    ("stretch", "warm_up"),
    ("foam roll", "warm_up"),
    ("cool down", "warm_up"),
    ("cooldown", "warm_up"),
]

_EQUIPMENT_KEYWORDS: list[tuple[str, str]] = [
    ("barbell", "barbell"),
    ("dumbbell", "dumbbell"),
    ("kettlebell", "kettlebell"),
    ("cable", "cable"),
    ("machine", "machine"),
    ("smith machine", "smith_machine"),
    ("smith", "smith_machine"),
    ("band", "band"),
    ("ez bar", "ez_bar"),
    ("ez-bar", "ez_bar"),
    ("trap bar", "trap_bar"),
]

# For each FIT category, a map from (equipment_keyword, *extra_keywords) -> exercise_name_string.
# Checked in order; first match wins.
_NAME_RULES: dict[str, list[tuple[list[str], str]]] = {
    "squat": [
        (["barbell", "front"], "barbell_front_squat"),
        (["barbell", "back"], "barbell_back_squat"),
        (["barbell", "hack"], "barbell_hack_squat"),
        (["barbell", "box"], "barbell_box_squat"),
        (["barbell"], "barbell_back_squat"),
        (["dumbbell", "front"], "dumbbell_front_squat"),
        (["dumbbell"], "dumbbell_squat"),
        (["goblet"], "goblet_squat"),
        (["kettlebell"], "kettlebell_squat"),
        (["sumo"], "sumo_squat"),
        (["pistol"], "pistol_squat"),
        (["leg press"], "leg_press"),
        (["step up"], "step_up"),
        (["air"], "air_squat"),
        (["thruster", "dumbbell"], "dumbbell_thrusters"),
        (["thruster"], "barbell_front_squat"),
        ([], "squat"),
    ],
    "lunge": [
        (["curtsy", "dumbbell"], "weighted_curtsy_lunge"),
        (["curtsy"], "curtsy_lunge"),
        (["reverse", "dumbbell"], "dumbbell_reverse_lunge"),
        (["reverse", "barbell"], "barbell_reverse_lunge"),
        (["reverse"], "reverse_lunge_with_reach_back"),
        (["lateral", "dumbbell"], "dumbbell_side_lunge"),
        (["lateral"], "sliding_lateral_lunge"),
        (["side", "dumbbell"], "dumbbell_side_lunge"),
        (["side"], "sliding_lateral_lunge"),
        (["walking", "dumbbell"], "walking_dumbbell_lunge"),
        (["walking"], "walking_lunge"),
        (["bulgarian", "dumbbell"], "dumbbell_bulgarian_split_squat"),
        (["bulgarian", "barbell"], "barbell_bulgarian_split_squat"),
        (["bulgarian"], "dumbbell_bulgarian_split_squat"),
        (["split"], "dumbbell_bulgarian_split_squat"),
        (["dumbbell"], "dumbbell_lunge"),
        (["barbell"], "barbell_lunge"),
        ([], "lunge"),
    ],
    "deadlift": [
        (["romanian", "dumbbell"], "dumbbell_straight_leg_deadlift"),
        (["romanian", "barbell"], "barbell_straight_leg_deadlift"),
        (["romanian"], "romanian_deadlift"),
        (["rdl"], "romanian_deadlift"),
        (["sumo"], "sumo_deadlift"),
        (["stiff", "dumbbell"], "dumbbell_straight_leg_deadlift"),
        (["stiff"], "barbell_straight_leg_deadlift"),
        (["straight leg"], "straight_leg_deadlift"),
        (["trap bar"], "trap_bar_deadlift"),
        (["dumbbell"], "dumbbell_deadlift"),
        (["kettlebell"], "kettlebell_deadlift"),
        (["barbell"], "barbell_deadlift"),
        (["rack pull"], "rack_pull"),
        ([], "barbell_deadlift"),
    ],
    "bench_press": [
        (["incline", "dumbbell"], "incline_dumbbell_bench_press"),
        (["incline", "barbell"], "incline_barbell_bench_press"),
        (["incline"], "incline_barbell_bench_press"),
        (["decline", "dumbbell"], "decline_dumbbell_bench_press"),
        (["close grip"], "close_grip_barbell_bench_press"),
        (["floor", "dumbbell"], "dumbbell_floor_press"),
        (["floor"], "barbell_floor_press"),
        (["dumbbell"], "dumbbell_bench_press"),
        (["smith"], "smith_machine_bench_press"),
        (["kettlebell"], "kettlebell_chest_press"),
        (["barbell"], "barbell_bench_press"),
        ([], "barbell_bench_press"),
    ],
    "shoulder_press": [
        (["arnold"], "arnold_press"),
        (["seated", "dumbbell"], "seated_dumbbell_shoulder_press"),
        (["seated", "barbell"], "seated_barbell_shoulder_press"),
        (["seated"], "seated_barbell_shoulder_press"),
        (["push press"], "barbell_push_press"),
        (["dumbbell"], "dumbbell_shoulder_press"),
        (["barbell"], "barbell_shoulder_press"),
        (["military"], "military_press"),
        ([], "overhead_dumbbell_press"),
    ],
    "row": [
        (["face pull"], "face_pull"),
        (["cable"], "seated_cable_row"),
        (["seated"], "seated_cable_row"),
        (["t bar"], "t_bar_row"),
        (["dumbbell", "one arm"], "one_arm_bent_over_row"),
        (["dumbbell", "single"], "one_arm_bent_over_row"),
        (["dumbbell"], "dumbbell_row"),
        (["barbell", "bent over"], "bent_over_row_with_barbell"),
        (["barbell"], "barbell_row"),
        ([], "dumbbell_row"),
    ],
    "pull_up": [
        (["lat pulldown"], "lat_pulldown"),
        (["wide grip", "pulldown"], "wide_grip_lat_pulldown"),
        (["wide grip"], "wide_grip_pull_up"),
        (["chin up"], "chin_up"),
        (["chinup"], "chin_up"),
        (["neutral"], "neutral_grip_pull_up"),
        (["weighted"], "weighted_pull_up"),
        ([], "pull_up"),
    ],
    "push_up": [
        (["diamond"], "diamond_push_up"),
        (["decline"], "decline_push_up"),
        (["incline"], "incline_push_up"),
        (["pike"], "pike_push_up"),
        (["wide"], "wide_grip_push_up"),
        (["kneeling"], "kneeling_push_up"),
        (["weighted"], "weighted_push_up"),
        ([], "push_up"),
    ],
    "curl": [
        (["hammer", "dumbbell"], "dumbbell_hammer_curl"),
        (["hammer"], "dumbbell_hammer_curl"),
        (["preacher"], "ez_bar_preacher_curl"),
        (["cable"], "cable_biceps_curl"),
        (["ez bar"], "standing_ez_bar_biceps_curl"),
        (["incline", "dumbbell"], "incline_dumbbell_biceps_curl"),
        (["dumbbell"], "dumbbell_biceps_curl"),
        (["barbell"], "barbell_biceps_curl"),
        ([], "dumbbell_biceps_curl"),
    ],
    "triceps_extension": [
        (["dip", "weighted"], "weighted_dip"),
        (["dip"], "body_weight_dip"),
        (["bench dip"], "bench_dip"),
        (["rope"], "rope_pressdown"),
        (["pushdown"], "triceps_pressdown"),
        (["pressdown"], "triceps_pressdown"),
        (["kickback"], "dumbbell_kickback"),
        (["skull"], "dumbbell_lying_triceps_extension"),
        (["overhead", "dumbbell"], "overhead_dumbbell_triceps_extension"),
        (["overhead", "cable"], "cable_overhead_triceps_extension"),
        (["overhead"], "overhead_dumbbell_triceps_extension"),
        (["cable"], "cable_overhead_triceps_extension"),
        (["dumbbell"], "dumbbell_lying_triceps_extension"),
        ([], "triceps_pressdown"),
    ],
    "hip_raise": [
        (["hip thrust", "barbell"], "barbell_hip_thrust_with_bench"),
        (["hip thrust"], "barbell_hip_thrust_with_bench"),
        (["glute bridge"], "hip_raise"),
        (["single leg"], "single_leg_hip_raise"),
        (["kettlebell swing"], "kettlebell_swing"),
        ([], "hip_raise"),
    ],
    "hip_swing": [
        (["dumbbell"], "single_arm_dumbbell_swing"),
        (["kettlebell"], "single_arm_kettlebell_swing"),
        ([], "single_arm_kettlebell_swing"),
    ],
    "plyo": [
        (["jump squat"], "jump_squat"),
        (["box jump"], "box_jump"),
        (["dumbbell", "jump"], "dumbbell_jump_squat"),
        ([], "body_weight_jump_squat"),
    ],
    "total_body": [
        (["man maker"], "man_makers"),
        (["burpee", "box"], "burpee_box_jump"),
        ([], "burpee"),
    ],
    "crunch": [
        (["bicycle"], "bicycle_crunch"),
        (["reverse"], "reverse_crunch"),
        (["cable"], "cable_crunch"),
        (["toes to bar"], "toes_to_bar"),
        ([], "crunch"),
    ],
    "sit_up": [
        (["v up"], "v_up"),
        (["weighted"], "weighted_sit_up"),
        ([], "sit_up"),
    ],
    "plank": [
        (["side"], "side_plank"),
        (["mountain"], "mountain_climber"),
        (["weighted"], "weighted_plank"),
        ([], "plank"),
    ],
    "core": [
        (["russian twist"], "russian_twist"),
        (["ab wheel"], "kneeling_ab_wheel"),
        (["turkish"], "turkish_get_up"),
        (["l sit"], "l_sit"),
        ([], "russian_twist"),
    ],
    "leg_curl": [
        (["good morning", "barbell"], "seated_barbell_good_morning"),
        (["good morning"], "good_morning"),
        (["sliding"], "sliding_leg_curl"),
        ([], "leg_curl"),
    ],
    "leg_raise": [
        (["hanging", "knee"], "hanging_knee_raise"),
        (["hanging"], "hanging_leg_raise"),
        (["lying"], "lying_straight_leg_raise"),
        (["reverse"], "reverse_leg_raise"),
        ([], "hanging_leg_raise"),
    ],
    "calf_raise": [
        (["seated"], "seated_calf_raise"),
        (["standing", "dumbbell"], "standing_dumbbell_calf_raise"),
        (["standing", "barbell"], "standing_barbell_calf_raise"),
        (["standing"], "standing_calf_raise"),
        (["single leg"], "single_leg_standing_calf_raise"),
        ([], "standing_calf_raise"),
    ],
    "lateral_raise": [
        (["front", "cable"], "cable_front_raise"),
        (["front", "dumbbell"], "front_raise"),
        (["front"], "front_raise"),
        (["seated"], "seated_lateral_raise"),
        ([], "dumbbell_lateral_raise"),
    ],
    "shrug": [
        (["upright", "dumbbell"], "dumbbell_upright_row"),
        (["upright"], "barbell_upright_row"),
        (["dumbbell"], "dumbbell_shrug"),
        (["barbell"], "barbell_shrug"),
        ([], "dumbbell_shrug"),
    ],
    "flye": [
        (["cable"], "cable_crossover"),
        (["incline", "dumbbell"], "incline_dumbbell_flye"),
        (["incline"], "incline_dumbbell_flye"),
        (["dumbbell"], "dumbbell_flye"),
        (["kettlebell"], "kettlebell_flye"),
        ([], "dumbbell_flye"),
    ],
    "warm_up": [
        (["arm circle"], "arm_circles"),
        (["cat"], "cat_camel"),
        (["walkout"], "walkout"),
        (["high knee"], "walking_high_knees"),
        ([], "quadruped_rocking"),
    ],
}


def auto_map(hevy_title: str) -> tuple[str, str] | None:
    """
    Guess the FIT (category, exercise_name) for a Hevy exercise title
    using keyword heuristics.  Returns None only if no category matches.
    """
    if not hevy_title:
        return None

    low = _normalize_title(hevy_title)
    if not low:
        return None

    # 1. Determine FIT exercise category
    category = None
    for pattern, cat in _CATEGORY_PATTERNS:
        if pattern in low:
            category = cat
            break

    if category is None:
        return None

    # 2. Determine specific exercise name within category
    rules = _NAME_RULES.get(category, [])
    for keywords, name in rules:
        if all(kw in low for kw in keywords):
            return (category, name)

    # Fallback: category matched, use first rule's default (empty keywords)
    if rules:
        return (category, rules[-1][1])

    return (category, category)


# ---------------------------------------------------------------------------
# Mapping file update — auto-map new exercises and persist
# ---------------------------------------------------------------------------

def ensure_all_mapped(
    workouts: list[dict],
    mapping_path: Path | None = None,
) -> dict[str, dict[str, str]]:
    """
    Scan all exercises across the given workouts.  For any not yet in the
    mapping file, auto-map them and append to the file.

    Returns the (possibly updated) in-memory mapping dict, with cache
    invalidated so subsequent calls see the new entries.
    """
    global _mapping_cache_key

    p = (mapping_path or _default_mapping_path()).resolve()

    # Load the raw JSON (or start fresh)
    raw_exercises: list[dict[str, Any]] = []
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("exercises"), list):
                raw_exercises = [e for e in data["exercises"] if isinstance(e, dict)]
        except (json.JSONDecodeError, OSError):
            pass

    # Build a set of already-known keys (normalized title + template_id)
    known_titles = {
        _normalize_title(e.get("hevy_title") or "")
        for e in raw_exercises if e.get("hevy_title")
    }
    known_ids = {
        (e.get("hevy_template_id") or "").strip()
        for e in raw_exercises if e.get("hevy_template_id")
    }
    known_ids.discard("")

    added = 0
    for workout in workouts:
        if not isinstance(workout, dict):
            continue
        exercises = workout.get("exercises")
        if not isinstance(exercises, list):
            continue
        for ex in exercises:
            if not isinstance(ex, dict):
                continue

            title = (ex.get("name") or ex.get("title") or "").strip()
            template_id = (str(ex.get("exerciseTemplateId") or ex.get("exercise_template_id") or "")).strip() or None

            norm = _normalize_title(title)
            if norm and norm in known_titles:
                continue
            if template_id and template_id in known_ids:
                continue

            result = auto_map(title)
            if result is None:
                log.info("Could not auto-map exercise: '%s' (template %s)", title, template_id)
                continue

            cat, name = result
            entry: dict[str, str] = {
                "hevy_title": title,
                "category": cat,
                "name": name,
                "auto_mapped": "true",
            }
            if template_id:
                entry["hevy_template_id"] = template_id

            raw_exercises.append(entry)
            if norm:
                known_titles.add(norm)
            if template_id:
                known_ids.add(template_id)
            added += 1
            log.info("Auto-mapped: '%s' -> %s / %s", title, cat, name)

    if added > 0:
        content = json.dumps({"exercises": raw_exercises}, indent=2, ensure_ascii=False)
        _atomic_write(p, content)
        # Invalidate cache so next load_mapping picks up the new file
        _mapping_cache_key = None
        log.info("Updated mapping file with %d new exercise(s): %s", added, p)

    return load_mapping(mapping_path, use_cache=False)


# ---------------------------------------------------------------------------
# Legacy: record unmapped exercises to separate file
# ---------------------------------------------------------------------------

def record_unmapped(
    hevy_title: str,
    hevy_template_id: str | None,
    muscle_group: str | None = None,
    unmapped_path: Path | None = None,
    mapping_path: Path | None = None,
) -> None:
    """
    Record an exercise we couldn't auto-map at all, so you can add it manually.
    """
    p = (unmapped_path or _default_unmapped_path()).resolve()
    try:
        existing: list[dict[str, Any]] = []
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("unmapped"), list):
                existing = [e for e in raw["unmapped"] if isinstance(e, dict)]
        seen = {
            (_normalize_title(e.get("hevy_title", "") or ""), (e.get("hevy_template_id") or ""))
            for e in existing
        }
        entry_tuple = (_normalize_title(hevy_title or ""), (hevy_template_id or "").strip())
        if entry_tuple not in seen:
            existing.append({
                "hevy_title": (hevy_title or "").strip(),
                "hevy_template_id": (hevy_template_id or "").strip(),
                "muscle_group": (muscle_group or "").strip(),
            })
        content = json.dumps({"unmapped": existing}, indent=2)
        _atomic_write(p, content)
        log.debug("Recorded unmapped exercise: %s", hevy_title or hevy_template_id)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not write unmapped list to %s: %s", p, e)
