#!/usr/bin/env python3
"""
One-time helper: log in to Garmin Connect locally and save the session.

Run this on your local machine (not the server), then copy the saved
session directory to the remote host.  The sync script will use
garth.resume() instead of garth.login(), bypassing CAPTCHA/MFA.

Usage:
    pip install garth
    python garmin_login.py <email> <password> [session_dir]

The session is saved to ./garth_session/ by default.
Copy that directory to the remote host and set "garth_session_path"
in your users.json to point to it.
"""
import sys
import garth
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print("Usage: python garmin_login.py <email> <password> [session_dir]")
        sys.exit(1)

    email = sys.argv[1]
    password = sys.argv[2]
    session_dir = sys.argv[3] if len(sys.argv) > 3 else "./garth_session"

    print(f"Logging in to Garmin Connect as {email}...")
    try:
        garth.login(email, password)
    except Exception as e:
        print(f"Login failed: {e}")
        print("\nIf you see a CAPTCHA error, try:")
        print("  1. Log in to https://connect.garmin.com in your browser first")
        print("  2. Then run this script again immediately")
        sys.exit(1)

    Path(session_dir).mkdir(parents=True, exist_ok=True)
    garth.save(session_dir)
    print(f"Session saved to: {session_dir}/")
    print(f"\nNext steps:")
    print(f"  1. Copy '{session_dir}/' to the remote host")
    print(f"     e.g.: scp -r {session_dir} ha:/config/scripts/ktmb1-fitness/garth_session_alexey")
    print(f"  2. In users.json, set:")
    print(f'     "garth_session_path": "/config/scripts/ktmb1-fitness/garth_session_alexey"')
    print(f"  3. The sync script will reuse this session (and auto-refresh tokens)")


if __name__ == "__main__":
    main()
