# pyscript: hevy_garmin_sync.py
# Runs Hevy -> Garmin sync on a schedule by calling the shell_command.
# Requires: shell_command.hevy_garmin_sync defined in configuration.yaml
# (runs the sync in background so it doesn't hit the 60s shell_command timeout)

@time_trigger("cron(*/30 * * * *)")  # Every 30 minutes
def hevy_garmin_scheduled_sync():
    """Trigger Hevy->Garmin sync every 30 minutes."""
    service.call("shell_command", "hevy_garmin_sync", blocking=False)
