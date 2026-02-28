"""
Build a Garmin FIT activity file from a Hevy workout.

Self-contained binary encoder — no external FIT-encoding library needed.
Uses field definitions from the official FIT SDK Profile (v21.x).

FIT file structure for strength training:
  Header -> FileId -> Sport -> Event(start) -> Set* -> Lap -> Session -> Event(stop) -> Activity -> CRC
"""
import io
import logging
import struct
from datetime import datetime, timezone
from typing import Any, Optional

from sync.exercise_mapping import lookup

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FIT constants
# ---------------------------------------------------------------------------

FIT_EPOCH_OFFSET = 631065600  # seconds between Unix epoch and FIT epoch (1989-12-31)

# CRC-16 lookup table (FIT uses CRC-16/ARC)
_CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]

# Base types (high bit = endian-sensitive flag for multi-byte)
ENUM    = 0x00  # 1 byte
UINT8   = 0x02  # 1 byte
UINT16  = 0x84  # 2 bytes
SINT16  = 0x83  # 2 bytes
UINT32  = 0x86  # 4 bytes
SINT32  = 0x85  # 4 bytes
UINT16Z = 0x8B  # 2 bytes (zero = invalid)
UINT32Z = 0x8C  # 4 bytes (zero = invalid)
STRING  = 0x07  # variable

_STRUCT_FMT = {
    ENUM:    '<B',
    UINT8:   '<B',
    UINT16:  '<H',
    SINT16:  '<h',
    UINT32:  '<I',
    SINT32:  '<i',
    UINT16Z: '<H',
    UINT32Z: '<I',
}

_BASE_SIZE = {
    ENUM: 1, UINT8: 1,
    UINT16: 2, SINT16: 2,
    UINT32: 4, SINT32: 4,
    UINT16Z: 2, UINT32Z: 4,
}

_INVALID = {
    ENUM:    0xFF,
    UINT8:   0xFF,
    UINT16:  0xFFFF,
    SINT16:  0x7FFF,
    UINT32:  0xFFFFFFFF,
    SINT32:  0x7FFFFFFF,
    UINT16Z: 0x0000,
    UINT32Z: 0x00000000,
}

# Global message numbers
MESG_FILE_ID  = 0
MESG_SPORT    = 12
MESG_SESSION  = 18
MESG_LAP      = 19
MESG_EVENT    = 21
MESG_ACTIVITY = 34
MESG_SET      = 225

# Sport / sub-sport
SPORT_TRAINING        = 10
SUB_SPORT_STRENGTH    = 20

# File type
FILE_ACTIVITY = 4

# Manufacturer
MANUFACTURER_DEVELOPMENT = 255

# Event enums
EVENT_TIMER    = 0
EVENT_ACTIVITY = 26
EVENT_TYPE_START    = 0
EVENT_TYPE_STOP_ALL = 4

# Activity type
ACTIVITY_MANUAL = 0

# Lap trigger
LAP_TRIGGER_SESSION_END = 7

# Session trigger
SESSION_TRIGGER_ACTIVITY_END = 0

# Set type
SET_TYPE_REST   = 0
SET_TYPE_ACTIVE = 1

# fit_base_unit
UNIT_KILOGRAM = 1

# ---------------------------------------------------------------------------
# Exercise category / name enums (from FIT SDK Profile v21.x)
# ---------------------------------------------------------------------------
EXERCISE_CATEGORY = {
    "bench_press": 0, "calf_raise": 1, "cardio": 2, "carry": 3, "chop": 4,
    "core": 5, "crunch": 6, "curl": 7, "deadlift": 8, "flye": 9,
    "hip_raise": 10, "hip_stability": 11, "hip_swing": 12, "hyperextension": 13,
    "lateral_raise": 14, "leg_curl": 15, "leg_raise": 16, "lunge": 17,
    "olympic_lift": 18, "plank": 19, "plyo": 20, "pull_up": 21, "push_up": 22,
    "row": 23, "shoulder_press": 24, "shoulder_stability": 25, "shrug": 26,
    "sit_up": 27, "squat": 28, "total_body": 29, "triceps_extension": 30,
    "warm_up": 31, "run": 32, "banded_exercises": 37,
}

EXERCISE_NAME: dict[str, dict[str, int]] = {
    "bench_press": {
        "alternating_dumbbell_chest_press_on_swiss_ball": 0,
        "barbell_bench_press": 1, "barbell_board_bench_press": 2,
        "barbell_floor_press": 3, "close_grip_barbell_bench_press": 4,
        "decline_dumbbell_bench_press": 5, "dumbbell_bench_press": 6,
        "dumbbell_floor_press": 7, "incline_barbell_bench_press": 8,
        "incline_dumbbell_bench_press": 9, "kettlebell_chest_press": 12,
        "single_arm_dumbbell_bench_press": 21, "smith_machine_bench_press": 22,
        "wide_grip_barbell_bench_press": 25, "alternating_dumbbell_chest_press": 26,
    },
    "calf_raise": {
        "seated_calf_raise": 6, "standing_barbell_calf_raise": 17,
        "standing_calf_raise": 18, "standing_dumbbell_calf_raise": 20,
        "single_leg_standing_calf_raise": 15,
    },
    "core": {
        "abs_jabs": 0, "russian_twist": 46, "bicycle": 49, "hanging_l_sit": 75,
        "l_sit": 88, "turkish_get_up": 89,
    },
    "crunch": {
        "bicycle_crunch": 0, "cable_crunch": 1, "reverse_crunch": 46,
        "crunch": 83, "toes_to_bar": 81,
    },
    "curl": {
        "barbell_biceps_curl": 3, "cable_biceps_curl": 8, "cable_hammer_curl": 9,
        "dumbbell_hammer_curl": 16, "dumbbell_biceps_curl": 46,
        "incline_dumbbell_biceps_curl": 22, "ez_bar_preacher_curl": 19,
        "standing_dumbbell_biceps_curl": 37, "standing_ez_bar_biceps_curl": 38,
    },
    "deadlift": {
        "barbell_deadlift": 0, "barbell_straight_leg_deadlift": 1,
        "dumbbell_deadlift": 2, "dumbbell_straight_leg_deadlift": 4,
        "sumo_deadlift": 15, "trap_bar_deadlift": 17, "kettlebell_deadlift": 20,
        "romanian_deadlift": 23, "straight_leg_deadlift": 25,
    },
    "flye": {
        "cable_crossover": 0, "dumbbell_flye": 2,
        "incline_dumbbell_flye": 3, "kettlebell_flye": 4,
    },
    "hip_raise": {
        "barbell_hip_thrust_on_floor": 0, "barbell_hip_thrust_with_bench": 1,
        "hip_raise": 11, "kettlebell_swing": 23, "single_leg_hip_raise": 30,
        "clams": 44, "glute_bridge": 11,
    },
    "hip_swing": {
        "single_arm_kettlebell_swing": 0, "single_arm_dumbbell_swing": 1,
        "step_out_swing": 2,
    },
    "lateral_raise": {
        "dumbbell_lateral_raise": 34, "front_raise": 10,
        "seated_lateral_raise": 24, "cable_front_raise": 5,
        "dumbbell_v_raise": 9,
    },
    "leg_curl": {
        "leg_curl": 0, "good_morning": 2, "seated_barbell_good_morning": 3,
        "sliding_leg_curl": 6,
    },
    "leg_raise": {
        "hanging_knee_raise": 0, "hanging_leg_raise": 1,
        "lying_straight_leg_raise": 8, "reverse_leg_raise": 13,
    },
    "lunge": {
        "overhead_lunge": 0, "barbell_lunge": 10, "barbell_reverse_lunge": 11,
        "barbell_side_lunge": 12, "dumbbell_lunge": 21,
        "dumbbell_reverse_lunge_to_high_knee_and_press": 24,
        "dumbbell_side_lunge": 25, "lunge": 32, "weighted_lunge": 33,
        "reverse_lunge_with_reach_back": 48,
        "walking_lunge": 78, "weighted_walking_lunge": 79,
        "alternating_dumbbell_lunge": 81, "dumbbell_reverse_lunge": 82,
        "curtsy_lunge": 86, "weighted_curtsy_lunge": 87,
        "sliding_lateral_lunge": 74,
    },
    "plank": {
        "plank": 42, "weighted_plank": 117, "side_plank": 66,
        "mountain_climber": 46,
    },
    "plyo": {
        "body_weight_jump_squat": 3, "weighted_jump_squat": 4,
        "dumbbell_jump_squat": 9, "box_jump": 33, "jump_squat": 37,
    },
    "pull_up": {
        "pull_up": 38, "chin_up": 39, "weighted_pull_up": 24,
        "lat_pulldown": 13, "wide_grip_pull_up": 26,
        "wide_grip_lat_pulldown": 25, "neutral_grip_pull_up": 43,
    },
    "push_up": {
        "push_up": 77, "weighted_push_up": 40,
        "close_hands_push_up": 11, "decline_push_up": 13,
        "diamond_push_up": 15, "incline_push_up": 27,
        "kneeling_push_up": 33, "pike_push_up": 84,
        "wide_grip_push_up": 85,
    },
    "row": {
        "dumbbell_row": 2, "barbell_row": 45, "bent_over_row_with_barbell": 46,
        "bent_over_row_with_dumbell": 47, "cable_row_standing": 1,
        "seated_cable_row": 18, "t_bar_row": 28,
        "one_arm_bent_over_row": 13, "face_pull": 5,
    },
    "shoulder_press": {
        "alternating_dumbbell_shoulder_press": 0, "arnold_press": 1,
        "barbell_push_press": 3, "barbell_shoulder_press": 4,
        "dumbbell_push_press": 8, "overhead_barbell_press": 14,
        "overhead_dumbbell_press": 15, "seated_barbell_shoulder_press": 16,
        "seated_dumbbell_shoulder_press": 17,
        "dumbbell_shoulder_press": 24, "military_press": 25,
        "dumbbell_front_raise": 28,
    },
    "shrug": {
        "barbell_shrug": 1, "dumbbell_shrug": 5,
        "barbell_upright_row": 2, "dumbbell_upright_row": 6,
    },
    "sit_up": {
        "sit_up": 37, "weighted_sit_up": 34, "v_up": 31,
    },
    "squat": {
        "leg_press": 0, "back_squats": 2, "barbell_back_squat": 6,
        "barbell_box_squat": 7, "barbell_front_squat": 8,
        "barbell_hack_squat": 9, "dumbbell_front_squat": 27,
        "dumbbell_squat": 29, "goblet_squat": 37,
        "kettlebell_squat": 38, "pistol_squat": 47,
        "squat": 61, "weighted_squat": 62, "sumo_squat": 69,
        "air_squat": 100, "dumbbell_thrusters": 101,
    },
    "total_body": {
        "burpee": 0, "weighted_burpee": 1, "man_makers": 5,
    },
    "triceps_extension": {
        "bench_dip": 0, "body_weight_dip": 2,
        "cable_overhead_triceps_extension": 5,
        "dumbbell_kickback": 6, "dumbbell_lying_triceps_extension": 7,
        "overhead_dumbbell_triceps_extension": 15,
        "rope_pressdown": 19, "triceps_pressdown": 39,
        "weighted_dip": 40,
    },
    "warm_up": {
        "quadruped_rocking": 0, "arm_circles": 5, "cat_camel": 7,
        "walkout": 29, "walking_high_knees": 26,
        "walking_knee_hugs": 27, "walking_leg_cradles": 28,
    },
}


# ---------------------------------------------------------------------------
# FIT binary encoder
# ---------------------------------------------------------------------------

def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def _dt_to_fit(dt: datetime) -> int:
    """Convert a Python datetime to FIT timestamp (seconds since 1989-12-31 UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) - FIT_EPOCH_OFFSET


Field = tuple[int, int, int]  # (field_def_num, size_bytes, base_type)


class _FitWriter:
    """Low-level FIT binary writer."""

    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self._defs: dict[int, list[Field]] = {}

    def define(self, local_num: int, mesg_num: int, fields: list[Field]) -> None:
        header = (0x40 | (local_num & 0x0F)).to_bytes(1, "little")
        reserved = b"\x00"
        arch = b"\x00"  # little-endian
        mesg = struct.pack("<H", mesg_num)
        num_fields = struct.pack("<B", len(fields))
        field_bytes = b""
        for fdn, sz, bt in fields:
            field_bytes += struct.pack("<BBB", fdn, sz, bt)
        self._buf.write(header + reserved + arch + mesg + num_fields + field_bytes)
        self._defs[local_num] = fields

    def write(self, local_num: int, *values: Any) -> None:
        fields = self._defs[local_num]
        header = (local_num & 0x0F).to_bytes(1, "little")
        self._buf.write(header)
        for (fdn, sz, bt), val in zip(fields, values):
            if val is None:
                val = _INVALID[bt]
            self._buf.write(struct.pack(_STRUCT_FMT[bt], val & ((1 << (sz * 8)) - 1)))

    def finish(self) -> bytes:
        data = self._buf.getvalue()
        header_size = 14
        protocol_version = 0x20
        profile_version = 2195  # 21.95
        data_type = b".FIT"
        hdr = struct.pack("<BBHI4s", header_size, protocol_version,
                          profile_version, len(data), data_type)
        hdr_crc = struct.pack("<H", _crc16(hdr))
        full_header = hdr + hdr_crc
        file_crc = struct.pack("<H", _crc16(full_header + data))
        return full_header + data + file_crc


# ---------------------------------------------------------------------------
# Exercise mapping resolution
# ---------------------------------------------------------------------------

def _resolve_exercise(
    exercise_name: str,
    template_id: Optional[str],
    mapping_path: Any,
    mapping: Optional[dict],
) -> tuple[Optional[int], Optional[int]]:
    """Return (category_id, exercise_name_id) or (None, None) if unmapped."""
    result = lookup(exercise_name, template_id, mapping_path, mapping=mapping)
    if result is None:
        return None, None
    cat_str, name_str = result

    cat_key = cat_str.strip().lower().replace(" ", "_")
    cat_id = EXERCISE_CATEGORY.get(cat_key)
    if cat_id is None:
        log.warning("Unknown exercise category '%s' for '%s'", cat_str, exercise_name)
        return None, None

    name_key = name_str.strip().lower()
    cat_names = EXERCISE_NAME.get(cat_key, {})
    name_id = cat_names.get(name_key)
    if name_id is None:
        log.debug("Exercise name '%s' not in built-in enum for category '%s'; using category only",
                  name_str, cat_str)
    return cat_id, name_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def workout_to_fit(
    workout: dict[str, Any],
    mapping_path: Any = None,
    mapping: Optional[dict] = None,
) -> bytes:
    """
    Convert a Hevy API workout dict to Garmin FIT file bytes.

    The resulting .fit file is a valid strength-training activity that
    Garmin Connect will accept and display with exercise names, sets,
    reps, and weights.
    """
    start_raw = workout.get("startTime") or workout.get("start_time") or ""
    end_raw = workout.get("endTime") or workout.get("end_time") or ""
    log.debug("workout keys: %s", list(workout.keys())[:12])
    try:
        start_dt = datetime.fromisoformat(start_raw)
    except (ValueError, TypeError):
        start_dt = datetime.now(timezone.utc)
    try:
        end_dt = datetime.fromisoformat(end_raw)
    except (ValueError, TypeError):
        end_dt = start_dt

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    start_ts = _dt_to_fit(start_dt)
    end_ts = _dt_to_fit(end_dt)
    elapsed_ms = max(int((end_dt - start_dt).total_seconds() * 1000), 1000)

    exercises = workout.get("exercises", [])
    if not isinstance(exercises, list):
        exercises = []

    w = _FitWriter()

    # --- Local message 0: file_id (mesg 0) ---
    LM_FILEID = 0
    w.define(LM_FILEID, MESG_FILE_ID, [
        (0, 1, ENUM),      # type
        (1, 2, UINT16),    # manufacturer
        (2, 2, UINT16),    # product
        (3, 4, UINT32Z),   # serial_number
        (4, 4, UINT32),    # time_created
    ])
    w.write(LM_FILEID, FILE_ACTIVITY, MANUFACTURER_DEVELOPMENT, 1, 12345, start_ts)

    # --- Local message 1: sport (mesg 12) ---
    LM_SPORT = 1
    w.define(LM_SPORT, MESG_SPORT, [
        (0, 1, ENUM),  # sport
        (1, 1, ENUM),  # sub_sport
    ])
    w.write(LM_SPORT, SPORT_TRAINING, SUB_SPORT_STRENGTH)

    # --- Local message 2: event (mesg 21) ---
    LM_EVENT = 2
    w.define(LM_EVENT, MESG_EVENT, [
        (253, 4, UINT32),  # timestamp
        (0,   1, ENUM),    # event
        (1,   1, ENUM),    # event_type
        (3,   4, UINT32),  # data
    ])
    w.write(LM_EVENT, start_ts, EVENT_TIMER, EVENT_TYPE_START, 0)

    # --- Local message 3: set (mesg 225) ---
    LM_SET = 3
    w.define(LM_SET, MESG_SET, [
        (254, 4, UINT32),   # timestamp
        (0,   4, UINT32),   # duration (ms, scale 1000)
        (3,   2, UINT16),   # repetitions
        (4,   2, UINT16),   # weight (scale 16)
        (5,   1, ENUM),     # set_type
        (6,   4, UINT32),   # start_time
        (7,   2, UINT16),   # category (exercise_category)
        (8,   2, UINT16),   # category_subtype (exercise_name)
        (9,   2, UINT16),   # weight_display_unit
        (10,  2, UINT16),   # message_index
    ])

    total_sets = sum(
        len(ex.get("sets", [])) for ex in exercises if isinstance(ex, dict)
    )
    if total_sets == 0:
        total_sets = 1

    time_per_set = elapsed_ms // total_sets
    current_ts = start_ts
    set_index = 0

    for ex in exercises:
        if not isinstance(ex, dict):
            continue

        ex_name = (ex.get("name") or ex.get("title") or "").strip()
        template_id = ex.get("exerciseTemplateId") or ex.get("exercise_template_id")
        if template_id is not None:
            template_id = str(template_id).strip() or None

        cat_id, name_id = _resolve_exercise(ex_name, template_id, mapping_path, mapping)

        sets = ex.get("sets", [])
        if not isinstance(sets, list):
            continue

        for s in sets:
            if not isinstance(s, dict):
                continue

            set_start = current_ts
            duration_ms = time_per_set
            reps = s.get("reps")
            weight_kg = s.get("weight") if s.get("weight") is not None else s.get("weight_kg")
            set_type_str = (s.get("type") or "normal").lower()

            if set_type_str in ("rest",):
                fit_set_type = SET_TYPE_REST
            else:
                fit_set_type = SET_TYPE_ACTIVE

            if s.get("duration") is not None:
                try:
                    duration_ms = int(float(s["duration"]) * 1000)
                except (ValueError, TypeError):
                    pass

            fit_reps = None
            if reps is not None:
                try:
                    fit_reps = max(0, int(reps))
                except (ValueError, TypeError):
                    fit_reps = None

            fit_weight = None
            if weight_kg is not None:
                try:
                    fit_weight = max(0, int(round(float(weight_kg) * 16)))
                except (ValueError, TypeError):
                    fit_weight = None

            current_ts = set_start + max(duration_ms // 1000, 1)

            w.write(
                LM_SET,
                current_ts,                         # timestamp (end of set)
                max(duration_ms, 0),                 # duration
                fit_reps,                            # repetitions
                fit_weight,                          # weight
                fit_set_type,                        # set_type
                set_start,                           # start_time
                cat_id if cat_id is not None else None,
                name_id if name_id is not None else None,
                UNIT_KILOGRAM,                       # weight_display_unit
                set_index,                           # message_index
            )
            set_index += 1

    final_ts = max(current_ts, end_ts)

    # --- Event: stop ---
    w.write(LM_EVENT, final_ts, EVENT_TIMER, EVENT_TYPE_STOP_ALL, 0)

    # --- Local message 4: lap (mesg 19) ---
    LM_LAP = 4
    w.define(LM_LAP, MESG_LAP, [
        (254, 2, UINT16),   # message_index
        (253, 4, UINT32),   # timestamp
        (0,   1, ENUM),     # event
        (1,   1, ENUM),     # event_type
        (2,   4, UINT32),   # start_time
        (7,   4, UINT32),   # total_elapsed_time (ms, scale 1000)
        (8,   4, UINT32),   # total_timer_time (ms, scale 1000)
        (25,  1, ENUM),     # sport
        (39,  1, ENUM),     # sub_sport
        (24,  1, ENUM),     # lap_trigger
    ])
    w.write(LM_LAP,
            0,                   # message_index
            final_ts,            # timestamp
            EVENT_TIMER,         # event
            EVENT_TYPE_STOP_ALL, # event_type
            start_ts,            # start_time
            elapsed_ms,          # total_elapsed_time
            elapsed_ms,          # total_timer_time
            SPORT_TRAINING,      # sport
            SUB_SPORT_STRENGTH,  # sub_sport
            LAP_TRIGGER_SESSION_END)

    # --- Local message 5: session (mesg 18) ---
    LM_SESSION = 5
    w.define(LM_SESSION, MESG_SESSION, [
        (253, 4, UINT32),   # timestamp
        (2,   4, UINT32),   # start_time
        (7,   4, UINT32),   # total_elapsed_time (ms, scale 1000)
        (8,   4, UINT32),   # total_timer_time (ms, scale 1000)
        (5,   1, ENUM),     # sport
        (6,   1, ENUM),     # sub_sport
        (25,  2, UINT16),   # first_lap_index
        (26,  2, UINT16),   # num_laps
        (28,  1, ENUM),     # trigger
    ])
    w.write(LM_SESSION,
            final_ts,
            start_ts,
            elapsed_ms,
            elapsed_ms,
            SPORT_TRAINING,
            SUB_SPORT_STRENGTH,
            0,                              # first_lap_index
            1,                              # num_laps
            SESSION_TRIGGER_ACTIVITY_END)

    # --- Local message 6: activity (mesg 34) ---
    LM_ACTIVITY = 6
    w.define(LM_ACTIVITY, MESG_ACTIVITY, [
        (253, 4, UINT32),   # timestamp
        (0,   4, UINT32),   # total_timer_time (ms, scale 1000)
        (1,   2, UINT16),   # num_sessions
        (2,   1, ENUM),     # type
        (3,   1, ENUM),     # event
        (4,   1, ENUM),     # event_type
    ])
    w.write(LM_ACTIVITY,
            final_ts,
            elapsed_ms,
            1,                    # num_sessions
            ACTIVITY_MANUAL,      # type
            EVENT_ACTIVITY,       # event
            EVENT_TYPE_STOP_ALL)  # event_type

    return w.finish()
