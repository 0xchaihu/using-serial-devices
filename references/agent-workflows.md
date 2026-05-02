# Agent Workflows

## Pick a port

Run `list --json`. Use a port only when the selection is unambiguous: the user gave an exact port, or exactly one port is available. In every other case, ask the user to choose before probing, reading, or writing. Present enough metadata (`port`, `description`, `manufacturer`, `product`, VID/PID) to make the choice clear. Do not auto-select based on descriptions, platform conventions, or likely matches. On macOS, prefer `/dev/cu.*` for initiating connections after the user has chosen the matching device.

## Capture logs

Use `read --until-idle N --json` when the device emits a burst and then quiets. Use `read --expect REGEX --json` when the task has a known success token. Use `--log PATH` when the user wants the raw transcript saved.

## Send a command and wait

Use `request` instead of separate `write` and `read` whenever possible. It opens the port once, sends the payload, waits by `--expect`, `--until-idle`, byte count, line count, or time, and returns one JSON result. If `--expect` is set and not matched, treat `ok:false` / exit `3` as a failed device response.

Recommended examples:

```bash
python scripts/serial_tool.py request --port COM5 --baud 115200 --text "AT" --newline crlf --expect "OK" --until-idle 1 --json
python scripts/serial_tool.py request --port /dev/ttyACM0 --baud 115200 --hex "55 aa 00 ff" --max-bytes 16 --json
```

## Monitor while working

Start `monitor` before foreground work that may trigger serial output. Poll after each meaningful foreground step. Use `wait` when the next step depends on a serial event.

Keep and reuse `next_offset` so each poll returns only new events. If `monitor start` returns `ok:false`, do not continue; inspect the structured error.

## Safety

Do not send destructive commands blindly. For bootloaders, flash tools, factory reset commands, motor movement, voltage/current changes, or irreversible settings, show the exact payload and ask the user to confirm.
