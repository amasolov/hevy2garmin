# Running Hevy -> Garmin sync from Home Assistant (PyScript)

Run the sync as a **scheduled job** using PyScript and a shell command.

## Why PyScript + shell_command?

- **PyScript** gives you a clean `@time_trigger("cron(...)")` schedule in Python.
- The actual sync runs in a **shell_command** so it uses your own Python environment (venv with `garth`, `requests`, etc.). Home Assistant's shell_command has a **60 second timeout**; the sync runs in the **background** (`nohup ... &`) so the command returns immediately and the sync keeps running.

## Setup

### 1. Install PyScript

- Install **PyScript** via HACS (or manually): [PyScript docs](https://hacs-pyscript.readthedocs.io/).
- Add `pyscript:` to your HA config (or configure via UI) so PyScript loads.

### 2. Copy the sync code where HA can run it

Copy this repo (or at least the `sync/` package, `run_sync.py`, and `exercise_mapping.json`) to a directory your Home Assistant can read and execute, for example:

- **Supervised / Core**: e.g. `/config/scripts/ktmb1-fitness/`
- **Container**: ensure that path is in your config volume.

Create a virtualenv and install dependencies:

```bash
cd /config/scripts/ktmb1-fitness
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Configure users

1. Copy `users.json.example` to `users.json` in the script directory.
2. Edit `users.json`: for each user set `id`, `hevy_api_key`, `garmin_email`, `garmin_password`.
3. Set `garth_session_path` if using a pre-created garth session (recommended for MFA accounts).

**Do not commit `users.json`** -- keep credentials only on the machine.

### 4. Set up Garmin authentication

If your Garmin account has MFA or you get CAPTCHA errors from the server:

1. Run `garmin_login.py` **locally** (on your laptop, not the server):
   ```bash
   python garmin_login.py <email> <password> ./garth_session_myname
   ```
2. Copy the session directory to HA:
   ```bash
   scp -r ./garth_session_myname ha:/config/scripts/ktmb1-fitness/
   ```
3. Set `garth_session_path` in `users.json`:
   ```json
   "garth_session_path": "/config/scripts/ktmb1-fitness/garth_session_myname"
   ```

The sync script uses `garth.resume()` with OAuth refresh tokens, so MFA is only needed once. Running the sync every 30 minutes keeps the tokens fresh.

### 5. Add the shell command to Home Assistant

In `configuration.yaml` (or an included file):

```yaml
shell_command:
  hevy_garmin_sync: >-
    nohup /config/scripts/ktmb1-fitness/.venv/bin/python
    /config/scripts/ktmb1-fitness/run_sync.py
    >> /config/scripts/ktmb1-fitness/sync.log 2>&1 &
```

### 6. Add the PyScript scheduled job

Copy the PyScript file to `<config>/pyscript/hevy_garmin_sync.py`:

```python
@time_trigger("cron(*/30 * * * *)")  # Every 30 minutes
def hevy_garmin_scheduled_sync():
    """Trigger Hevy->Garmin sync every 30 minutes."""
    service.call("shell_command", "hevy_garmin_sync", blocking=False)
```

Reload PyScript (or restart HA) so the new script is loaded.

### 7. Check logs

- **Sync script**: `tail -f /config/scripts/ktmb1-fitness/sync.log`
- **Home Assistant**: Developer Tools -> Logs, or check the PyScript log for the trigger.

## Summary

| What                    | Where / How |
|-------------------------|-------------|
| Schedule                | PyScript `@time_trigger("cron(*/30 * * * *)")` in `hevy_garmin_sync.py` |
| Actual sync             | `run_sync.py` via `shell_command.hevy_garmin_sync` |
| Runs in background      | `nohup ... &` (avoids 60s timeout) |
| Users & credentials     | `users.json` (copy from `users.json.example`) |
| Garmin auth             | `garth_session_path` in `users.json` (create locally with `garmin_login.py`) |
| Exercise mapping        | `exercise_mapping.json` (auto-updated each run with new exercises) |
| Logs                    | `sync.log` next to the script |
| Already-synced list     | `hevy_garmin_synced_{id}.json` per user |
