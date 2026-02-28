# ktmb1-fitness

Sync workouts from **Hevy** to **Garmin Connect** (strength training FIT activities).

## Status

- **Hevy**: fetch workouts via official API v1
- **Garmin**: upload FIT via garth
- **FIT builder**: map Hevy exercises to Garmin strength FIT (TODO)
- **Home Assistant**: run as scheduled job via PyScript

## How sync works

- Each run fetches **recent** Hevy workouts (last N days, default 7).
- **Already-synced** workouts are **skipped**: a state file (`hevy_garmin_synced_{id}.json`) stores Hevy workout IDs that were uploaded to Garmin. No duplicates.
- New workouts are uploaded once; their IDs are added to the state file.

**Hevy to Garmin exercise mapping** is kept updated in two ways:
1. **Mapping file** (`exercise_mapping.json`): maps Hevy exercise name or `exerciseTemplateId` to Garmin category + name. Copy `exercise_mapping.json.example` to `exercise_mapping.json` and extend it.
2. **Unmapped list** (`unmapped_exercises.json`): every run, any Hevy exercise not in the mapping is appended here so you can add it later.

Per-user overrides in the users file: `state_file`, `mapping_file`, `unmapped_file`, `days_back`, `garth_session_path`. Optional env: `HEVY_GARMIN_USERS_FILE` to point to a users file outside the project.

## Quick start (CLI)

**All config is in a users file** (one user or many, same format):

```bash
pip install -r requirements.txt
cp users.json.example users.json
# Edit users.json: set id, hevy_api_key, garmin_email, garmin_password for each user
python run_sync.py
```

The script looks for `users.json` in the project root. To use another path:

```bash
export HEVY_GARMIN_USERS_FILE="/path/to/users.json"
python run_sync.py
```

Each user gets a **separate state file** (`hevy_garmin_synced_{id}.json` by default). Users are synced one by one; if one fails, the rest still run (exit code 1 if any failed).

## Run from Home Assistant (PyScript)

Use PyScript to run the sync on a schedule (e.g. daily at 8am). The script runs in a **shell_command** in the background so it doesn't hit the 60s timeout.

See **[homeassistant/README.md](homeassistant/README.md)** for setup details.

## Project layout

```
sync/                 # Sync package
  config.py           # Users file config (single or multi user)
  hevy_client.py      # Hevy official API v1 client
  garmin_upload.py    # Garmin upload via garth
  fit_builder.py      # Hevy to FIT (TODO)
  run_sync.py         # Main loop (one user or many, one by one)
run_sync.py           # CLI entrypoint
users.json            # Your user(s) - copy from users.json.example
users.json.example    # Example (one or more users)
homeassistant/
  pyscript/
    hevy_garmin_sync.py   # Scheduled trigger
  configuration.yaml.example
  README.md
```

## Hevy API key

Get your Hevy API key from your Hevy account settings (API Access section). This is the official v1 API key used in the `api-key` header.
