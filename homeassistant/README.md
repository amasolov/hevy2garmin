# Running Hevy → Garmin sync from Home Assistant (PyScript)

Run the sync as a **scheduled job** using PyScript and a shell command.

## Why PyScript + shell_command?

- **PyScript** gives you a clean `@time_trigger("cron(...)")` schedule in Python.
- The actual sync runs in a **shell_command** so it uses your own Python environment (venv with `garth`, `requests`, etc.). Home Assistant’s shell_command has a **60 second timeout**; the sync runs in the **background** (`nohup ... &`) so the command returns immediately and the sync keeps running.

## Setup

### 1. Install PyScript

- Install **PyScript** via HACS (or manually): [PyScript docs](https://hacs-pyscript.readthedocs.io/).
- Add `pyscript:` to your HA config (or configure via UI) so PyScript loads.

### 2. Copy the sync code where HA can run it

Copy this repo (or at least the `sync/` package and `run_sync.py`) to a directory your Home Assistant can read and execute, for example:

- **Supervised / Core**: e.g. `/config/scripts/ktmb1-fitness/`
- **Container**: ensure that path is in your config volume.

Create a virtualenv and install dependencies (recommended):

```bash
cd /config/scripts/ktmb1-fitness
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then point the shell command at the venv Python:

```yaml
shell_command:
  hevy_garmin_sync: 'nohup /config/scripts/ktmb1-fitness/.venv/bin/python /config/scripts/ktmb1-fitness/run_sync.py >> /config/scripts/ktmb1-fitness/sync.log 2>&1 &'
```

### 3. Configure users (users file only)

The sync reads **only** from a users file (same for one or many users).

1. Copy `users.json.example` to `users.json` in the script directory (e.g. `/config/scripts/ktmb1-fitness/users.json`).
2. Edit `users.json`: for each user set `id`, `hevy_api_key`, `garmin_email`, `garmin_password`.
3. If the file is not in the project root, set the path when running, e.g. in your shell command wrapper: `export HEVY_GARMIN_USERS_FILE=/config/scripts/ktmb1-fitness/users.json`.

The script looks for `users.json` in the project root by default, so if you copied the repo to `/config/scripts/ktmb1-fitness/`, placing `users.json` there is enough. **Do not commit `users.json`** (add it to `.gitignore`); keep credentials only on the machine.

### 4. Add the shell command to Home Assistant

In `configuration.yaml` (or an included file), add:

```yaml
shell_command:
  hevy_garmin_sync: 'nohup /path/to/your/run_sync.py >> /path/to/sync.log 2>&1 &'
```

Use the path and Python (venv or system) you chose above. If your install uses **allowlist_external_commands**, add the path to the script or to the shell that runs it.

### 5. Add the PyScript scheduled job

Copy the PyScript file into your HA pyscript directory:

- **Default**: `<config>/pyscript/hevy_garmin_sync.py`

Content (same as in this repo):

```python
# pyscript: hevy_garmin_sync.py
@time_trigger("cron(0 8 * * *)")  # Every day at 08:00 local time
def hevy_garmin_scheduled_sync():
    """Trigger Hevy->Garmin sync daily at 8am."""
    service.call("shell_command", "hevy_garmin_sync", blocking=False)
```

Change the cron expression if you want a different schedule, e.g.:

- `"cron(0 7 * * 1-5)"` – 07:00 on weekdays  
- `"cron(30 22 * * *)"` – 22:30 every day  

Reload PyScript (or restart HA) so the new script is loaded.

### 6. Check logs

- **Sync script**: `tail -f /config/scripts/ktmb1-fitness/sync.log`
- **Home Assistant**: Developer Tools → Logs, or check the PyScript log for the trigger.

## Summary

| What                | Where / How |
|---------------------|-------------|
| Schedule            | PyScript `@time_trigger("cron(...)")` in `hevy_garmin_sync.py` |
| Actual sync         | `run_sync.py` (this repo), run via `shell_command.hevy_garmin_sync` |
| Runs in background  | `nohup ... &` so the shell returns immediately (avoids 60s timeout) |
| **Users & credentials** | Single `users.json` file (copy from `users.json.example`). One or more users, same format. Optional `HEVY_GARMIN_USERS_FILE` if the file is elsewhere. |
| Logs                | `sync.log` next to the script |
| Already-synced list  | `hevy_garmin_synced_{id}.json` per user (override with `state_file` in users JSON) |

After FIT encoding is implemented in `sync/fit_builder.py`, the same flow will fetch Hevy workouts, build FIT files, and upload them to Garmin on your schedule.
