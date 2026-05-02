---
name: using-serial-devices
description: Use when a coding agent needs to discover serial ports, choose among TTY/COM/UART devices, read or write serial data, debug embedded devices, collect serial logs, run AT/bootloader/REPL commands, maintain a serial monitor, or open a serial terminal across Windows, Linux, and macOS.
---

# Using Serial Devices

Use this skill to interact with serial devices through a stable, agent-friendly CLI. Prefer structured commands over ad-hoc terminal programs so results are easy to parse and resume.

## Default workflow

1. List ports first unless the user already gave an exact port:
   `python scripts/serial_tool.py list --json`
2. Use a port only when it is unambiguous: the user gave an exact port (for example `COM5` or `/dev/ttyUSB0`) or `list --json` returns exactly one available port. Otherwise, stop and ask the user to choose which device to use. Show enough metadata (`port`, `description`, `manufacturer`, `product`, VID/PID) for an informed choice; do not infer, rank, or guess.
3. Probe before long interactions:
   `python scripts/serial_tool.py probe --port PORT --baud 115200 --json`
4. Use `request` for one command plus response, `read` for bounded capture, `monitor` for background capture, and `terminal` only for human interactive debugging.
5. Before writing, confirm the selected port, baud rate, payload, encoding, and newline policy when the command could change device state.

Default serial parameters: `--baud 115200 --bytesize 8 --parity N --stopbits 1 --timeout 1.0`.

Note: structured commands default to JSON output, and most agent workflows should still pass `--json` explicitly for clarity. `terminal` mode is human-oriented and prints serial output directly. `write` defaults `--newline` to `none` (raw, no line ending appended); `request` defaults `--newline` to `lf` (appends `\n`).

## CLI contract

Run all operations through `scripts/serial_tool.py`:

- `list --json`: return available ports with `port`, `description`, `hwid`, `vid`, `pid`, `serial_number`, `manufacturer`, and `product`.
- `probe --port PORT --baud BAUD --json`: open and close the port to verify access.
- `read --port PORT --baud BAUD [condition] --json|--jsonl`: capture incoming bytes.
- `write --port PORT --baud BAUD (--text TEXT | --hex "01 02 0A") --newline none|lf|crlf|cr --json`: send text or raw bytes (`--text` and `--hex` are mutually exclusive; one is required).
- `request --port PORT --baud BAUD (--text TEXT | --hex "01 02 0A") --newline lf|none|crlf|cr --expect REGEX --json`: send and wait for response (`--text` and `--hex` are mutually exclusive; one is required).
- `monitor start|poll|wait|status|stop`: run a background serial reader and query it later.
- `terminal --port PORT --baud BAUD`: simple line-oriented human terminal; serial output is printed while stdin lines are forwarded, and `:exit` quits.

JSON success shape: `{"ok":true,"command":"...","data":...}`.
JSON failure shape: `{"ok":false,"command":"...","error":{"type":"...","message":"...","suggestion":"..."}}`.
Treat JSON/JSONL as the stable interface; do not parse human prose.

Exit codes: `0` success, `1` argument error, `2` port unavailable, `3` timeout or expected pattern not matched, `4` read/write failure, `5` missing dependency.

## Read modes

Do not rely only on fixed sleeps. Choose a condition that matches the task:

- `--seconds N`: stop after wall-clock time.
- `--until-idle N`: stop after N seconds with no new data.
- `--max-bytes N`: stop after enough bytes.
- `--max-lines N`: stop after enough decoded lines.
- `--expect REGEX`: stop after matching output.
- `--forever --jsonl`: stream until interrupted.

Examples:

```bash
python scripts/serial_tool.py read --port COM5 --baud 115200 --until-idle 2 --json
python scripts/serial_tool.py read --port /dev/ttyUSB0 --baud 115200 --expect "boot complete" --json
python scripts/serial_tool.py request --port /dev/cu.usbserial-0001 --baud 115200 --text "AT" --newline crlf --expect "OK" --until-idle 1 --json
```

## Background monitor workflow

Use `monitor` when the agent should continue foreground work while serial data is collected in the background. Most coding-agent environments cannot be push-interrupted by background serial output, so use `poll` or `wait` at natural checkpoints.

1. Start capture:
   `python scripts/serial_tool.py monitor start --port PORT --baud 115200 --json`
   Optional: `--startup-timeout SECONDS` (default 2.0) to wait for the worker to open the port; `--chunk-size BYTES` (default 256) per read call.
2. Save the returned `session_id` and initial `next_offset`; `monitor start` only succeeds after the worker opens the port.
3. Continue foreground work such as build, flash, or file inspection.
4. Check without blocking:
   `python scripts/serial_tool.py monitor poll --session SESSION --since OFFSET --json`
5. Wait for new data or a pattern:
   `python scripts/serial_tool.py monitor wait --session SESSION --expect "ready|OK|boot complete" --timeout 30 --since OFFSET --json`
   When `--expect` is provided, success requires a regex match; unrelated events do not count as success.
6. Stop capture at the end:
   `python scripts/serial_tool.py monitor stop --session SESSION --json`

The monitor writes JSONL events and raw logs under the system temp directory. Event types include `open`, `data`, `line`, `match`, `error`, and `close`.

## Dependencies and platform notes

This skill requires Python 3 and `pyserial`. If the script returns `MissingDependency`, ask for approval according to the active environment policy before installing with `python -m pip install pyserial`.

Read `references/agent-workflows.md` for task patterns. Read `references/serial-platforms.md` for OS-specific port names, permissions, and newline guidance.
