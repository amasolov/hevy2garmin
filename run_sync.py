#!/usr/bin/env python3
"""
Entrypoint for Hevy -> Garmin sync. Run from CLI or from Home Assistant shell_command.

  python run_sync.py

Requires env: HEVY_AUTH_TOKEN, GARMIN_EMAIL, GARMIN_PASSWORD
Optional: HEVY_GARMIN_DAYS_BACK (default 7), GARTH_SESSION_PATH
"""
import os
import sys

# Ensure project root is on path when run from any cwd (e.g. by Home Assistant)
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from sync.run_sync import run_sync

if __name__ == "__main__":
    run_sync()
