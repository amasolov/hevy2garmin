# hevy2garmin

Sync workouts from **Hevy** to **Garmin Connect** as strength training FIT activities.

## Features

- **Hevy**: fetch workouts via official API v1 (snake_case responses auto-normalized)
- **Garmin**: upload FIT files via `garth` (OAuth session persistence, no re-login needed)
- **FIT builder**: custom binary encoder producing valid strength training activities with exercise names, sets, reps, and weights
- **Exercise mapping**: auto-maps Hevy exercises to Garmin FIT categories using keyword heuristics; unknown exercises are recorded for manual review
- **Multi-user**: configure one or many users in `users.json`; each synced independently
- **Home Assistant**: runs as a scheduled job (every 30 min) via PyScript + shell_command

## How sync works

1. Fetches **recent** Hevy workouts (last N days, default 7).
2. **Already-synced** workouts are skipped via a state file (`hevy_garmin_synced_{id}.json`). No duplicates.
3. New exercises are **auto-mapped** to Garmin FIT categories/names using keyword heuristics and saved to `exercise_mapping.json`.
4. Workouts are converted to FIT files and uploaded to Garmin Connect.

## Quick start (CLI)

```bash
pip install -r requirements.txt
cp users.json.example users.json
cp exercise_mapping.json.example exercise_mapping.json
# Edit users.json: set id, hevy_api_key, garmin_email, garmin_password
python run_sync.py
```

### Garmin MFA / CAPTCHA

If your Garmin account has MFA enabled (or login is blocked by CAPTCHA on a server), create a session locally first:

```bash
python garmin_login.py <garmin_email> <garmin_password> ./garth_session_myname
```

This prompts for MFA interactively. Then copy the session to the remote host and set `garth_session_path` in `users.json`. The sync script uses `garth.resume()` to authenticate without re-logging in; OAuth tokens auto-refresh.

### Environment variable

The script looks for `users.json` in the project root. To use another path:

```bash
export HEVY_GARMIN_USERS_FILE="/path/to/users.json"
python run_sync.py
```

## Exercise mapping

Hevy exercise names are mapped to Garmin FIT exercise categories and names:

1. **`exercise_mapping.json`**: static mapping file (title or template ID -> Garmin category + name). Seed it from `exercise_mapping.json.example`.
2. **Auto-mapping**: on each run, new exercises are automatically mapped using keyword heuristics and appended to the mapping file.
3. **`unmapped_exercises.json`**: exercises that couldn't be auto-mapped are recorded here for manual review.

## Run from Home Assistant

Uses PyScript to trigger a shell_command every 30 minutes. See **[homeassistant/README.md](homeassistant/README.md)** for setup details.

## Project layout

```
sync/                  # Core sync package
  config.py            # User config from users.json
  hevy_client.py       # Hevy official API v1 client (with snake_case normalization)
  garmin_upload.py     # Garmin upload via garth (auth-once pattern)
  fit_builder.py       # Binary FIT encoder for strength training activities
  exercise_mapping.py  # Auto-mapping + lookup for Hevy -> Garmin exercises
  run_sync.py          # Main orchestration (fetch, map, build, upload)
  sync_state.py        # Track synced workout IDs
run_sync.py            # CLI entrypoint
garmin_login.py        # One-time helper to create a garth session locally (for MFA)
test_upload.py         # Test script: build + upload a single workout
users.json.example     # Example user config
exercise_mapping.json.example  # Seed exercise mapping (30 common exercises)
homeassistant/
  pyscript/hevy_garmin_sync.py  # Scheduled trigger (every 30 min)
  configuration.yaml.example
  README.md
```

## Hevy API key

Get your Hevy API key from [hevy.com/settings](https://hevy.com/settings?developer) (requires Hevy Pro). This is the official v1 API key used in the `api-key` header.
