# Long-running Serial Monitoring

## Contents

- Production preset
- Alert behavior
- Retention and recovery
- Starting after reboot
- Operational limits

## Production preset

Use a named session and an absolute durable log directory. Keep control events in the session directory returned by `monitor start`; archive both the exact raw log and the readable timestamped log from `--log-dir`.

```powershell
$env:SERIAL_ALERT_URL = "https://example.invalid/secret-webhook"
python scripts/serial_tool.py monitor start `
  --port COM5 --baud 115200 --session device-a `
  --log-dir C:\serial-logs\device-a `
  --log-max-bytes 10485760 --log-backups 5 `
  --reconnect --reconnect-delay 2 --reconnect-max-attempts 0 `
  --alert "ERROR|FATAL|panic|watchdog|HardFault" `
  --silence-timeout 60 --alert-cooldown 30 `
  --alert-webhook-env SERIAL_ALERT_URL --json
```

Do not commit webhook URLs or place them directly in reusable scripts. Environment variables reduce command-line exposure; use the operating system's secret storage for stronger protection.

## Alert behavior

Pattern rules are case-sensitive Python regular expressions evaluated on complete decoded lines. Use an inline flag such as `(?i)` for case-insensitive matching. Repeat `--alert` for separate rules.

`--alert-cooldown` applies per pattern and to silence alerts. A silence alert fires once, then rearms only after new serial data arrives. Webhook bodies contain the structured alert fields plus a human-readable `text` field suitable for generic receivers and Slack incoming webhooks.

Treat `notification_error` as a delivery failure, not a serial capture failure. Poll the session periodically even when a webhook is configured.

## Retention and recovery

Raw and timestamped logs rotate independently to `.1`, `.2`, and so on when `--log-max-bytes` is exceeded. `monitor status` reports both active files and their rotated backups. Rotation is disabled when the size is `0` or the backup count is `0`.

The timestamped log uses local system time and UTF-8. Every complete decoded line, including an empty line, is written as `[YYYY-MM-DD HH:MM:SS.mmm] TEXT`. The raw log remains unchanged for byte-exact diagnosis.
Mixed adjacent line-ending pairs (`\r\n` and `\n\r`) produce one line break even when split across reads. Repeated identical terminators still represent separate lines.

`--reconnect` covers serial open and read failures. A `reconnect_wait` event records each retry. A successful open resets the consecutive retry count. Set a finite `--reconnect-max-attempts` when a permanently missing device should cause the monitor to exit.

The JSONL event stream and session metadata remain under the system temporary directory and are coordination state, not the durable archive. After an OS restart, start a new session name or remove stale session state only after confirming its recorded PID is no longer alive.

## Starting after reboot

The CLI does not install services or scheduled tasks because that changes host startup configuration. For unattended operation, create a wrapper that uses the absolute Python path, absolute `serial_tool.py` path, explicit serial settings, durable `--log-dir`, and a secret supplied by the host. Register that wrapper with Windows Task Scheduler, systemd, or launchd only when the user explicitly authorizes the host change.

Before enabling startup, test the wrapper interactively, disconnect and reconnect the cable, force a log rotation, and verify webhook failure behavior.

## Operational limits

- One process must own a serial port at a time.
- A detached monitor cannot actively wake a completed coding-agent task; use webhook delivery or an external scheduler for that boundary.
- Size rotation applies to raw and timestamped serial logs. JSONL control events are not rotated so byte offsets used by `poll --since` remain stable.
- Pattern matching depends on the selected decoding. Use `--encoding hex` for binary protocols and prefer protocol-aware tooling when line regexes are insufficient.
