# Using Serial Devices Skill

A coding-agent skill for discovering, reading, writing, monitoring, and automating serial devices over TTY/COM ports across Windows, Linux, and macOS.

This skill is designed for agent-friendly serial workflows: stable CLI commands, structured JSON/JSONL output, explicit exit codes, bounded reads, request-response helpers, and background monitoring that agents can poll while doing other work.

## What It Does

- Lists available serial ports with device metadata.
- Probes whether a port can be opened.
- Reads serial data with flexible stop conditions.
- Sends text or raw HEX bytes.
- Sends a command and waits for a response in one request-response operation.
- Runs a background serial monitor and lets an agent poll or wait for new events.
- Provides a simple line-oriented terminal for manual debugging.

## Requirements

- Python 3
- [`pyserial`](https://pyserial.readthedocs.io/)

Install the runtime dependency:

```bash
python -m pip install pyserial
```

For validating skill metadata with helper tooling that reads YAML frontmatter, `PyYAML` may also be needed:

```bash
python -m pip install PyYAML
```

## Installation

Copy or clone this folder into the skills directory used by your coding-agent environment. Common locations include:

```text
~/.codex/skills/using-serial-devices
~/.claude/skills/using-serial-devices
~/.agents/skills/using-serial-devices
```

On Windows, examples include:

```text
C:\Users\<you>\.codex\skills\using-serial-devices
C:\Users\<you>\.claude\skills\using-serial-devices
C:\Users\<you>\.agents\skills\using-serial-devices
```

Use whichever directory your agent actually scans for skills. The required skill entrypoint is `SKILL.md`. The main serial helper is:

```bash
python scripts/serial_tool.py --help
```

## Quick Start

List available ports:

```bash
python scripts/serial_tool.py list --json
```

Use a port only when it is unambiguous: the user provided an exact port, or exactly one port is listed. Otherwise, the agent must ask the user to choose the device before probing, reading, or writing.

Probe a port:

```bash
python scripts/serial_tool.py probe --port COM5 --baud 115200 --json
```

Read until the device is idle for 2 seconds:

```bash
python scripts/serial_tool.py read --port COM5 --baud 115200 --until-idle 2 --json
```

Send an AT command and wait for `OK`:

```bash
python scripts/serial_tool.py request --port COM5 --baud 115200 --text "AT" --newline crlf --expect "OK" --until-idle 1 --json
```

Send raw bytes:

```bash
python scripts/serial_tool.py write --port COM5 --baud 115200 --hex "55 aa 00 ff" --json
```

## CLI Overview

All operations go through `scripts/serial_tool.py`.

| Command | Purpose |
| --- | --- |
| `list` | List available serial ports. |
| `probe` | Open and close a port to verify access. |
| `read` | Capture serial input with bounded stop conditions. |
| `write` | Send text or HEX bytes. |
| `request` | Send data and wait for a response in one operation. |
| `monitor start` | Start a background serial reader. |
| `monitor poll` | Non-blocking check for new monitor events. |
| `monitor wait` | Block until new data or a matching pattern arrives. |
| `monitor status` | Inspect a background monitor session. |
| `monitor stop` | Stop a background monitor session. |
| `terminal` | Open a simple line-oriented terminal. |

Default serial parameters are 115200 8N1:

```text
--baud 115200 --bytesize 8 --parity N --stopbits 1 --timeout 1.0
```

## Structured Output

The tool is intended for agents, so JSON is the stable interface.

Successful command:

```json
{"ok":true,"command":"list","data":{"ports":[],"count":0}}
```

Failed command:

```json
{"ok":false,"command":"probe","error":{"type":"SerialException","message":"...","suggestion":"..."}}
```

JSONL streaming events are used for long-running reads and background monitors:

```jsonl
{"event":"open","port":"COM5","baud":115200,"time":"..."}
{"event":"data","bytes":15,"text":"boot complete\r\n","hex":"62 6f 6f 74 ...","time":"..."}
{"event":"line","line":"boot complete","time":"..."}
{"event":"match","pattern":"boot complete","line":"boot complete","time":"..."}
{"event":"close","reason":"matched","received_bytes":15,"time":"..."}
```

## Exit Codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Argument error. |
| `2` | Port unavailable or could not be opened. |
| `3` | Timeout or expected pattern was not matched. |
| `4` | Read/write failure. |
| `5` | Missing dependency. |

When `--expect` is provided, success requires the pattern to match. Unrelated data or error events do not count as success.

## Read Modes

`read` and `request` support several stop conditions:

| Option | Behavior |
| --- | --- |
| `--seconds N` | Stop after N seconds. |
| `--until-idle N` | Stop after N seconds without new data. |
| `--max-bytes N` | Stop after receiving N bytes. |
| `--max-lines N` | Stop after receiving N complete lines. |
| `--expect REGEX` | Stop after decoded output matches a regex. |
| `--forever --jsonl` | Stream until interrupted. |

Examples:

```bash
python scripts/serial_tool.py read --port /dev/ttyUSB0 --baud 115200 --expect "boot complete" --json
python scripts/serial_tool.py read --port /dev/cu.usbserial-0001 --baud 115200 --max-lines 20 --jsonl
```

## Background Monitor

Use `monitor` when an agent should keep collecting serial data while it performs other foreground tasks such as building, flashing, or inspecting files.

Start a monitor:

```bash
python scripts/serial_tool.py monitor start --port COM5 --baud 115200 --json
```

The command returns a `session_id`, an `event_log`, a `raw_log`, and a `next_offset`.

Poll for new events without blocking:

```bash
python scripts/serial_tool.py monitor poll --session SESSION_ID --since OFFSET --json
```

Wait for a specific response:

```bash
python scripts/serial_tool.py monitor wait --session SESSION_ID --expect "ready|OK|boot complete" --timeout 30 --since OFFSET --json
```

Check status:

```bash
python scripts/serial_tool.py monitor status --session SESSION_ID --json
```

Stop the monitor:

```bash
python scripts/serial_tool.py monitor stop --session SESSION_ID --json
```

Background monitor sessions store JSONL events and raw serial logs under the system temporary directory.

## Terminal Mode

The terminal mode is intentionally simple and portable. It prints serial output while forwarding stdin lines to the serial port.

```bash
python scripts/serial_tool.py terminal --port COM5 --baud 115200
```

Type `:exit` or `:quit` to leave the terminal. This is line-oriented, not a full raw-mode TUI.

## Platform Notes

### Windows

Ports usually look like `COM3`, `COM4`, etc. Pyserial handles `COM10` and above. If a port cannot be opened, another program such as an IDE, debugger, or serial terminal may already own it.

### Linux

Common ports include `/dev/ttyUSB0`, `/dev/ttyACM0`, and `/dev/serial/by-id/*`. Permission errors usually mean the user needs access to a serial group such as `dialout` or `uucp`.

### macOS

Prefer `/dev/cu.*` for agent-initiated connections. Matching `/dev/tty.*` devices may exist, but `/dev/cu.*` is usually better for outbound serial sessions.

## Safety Guidance

Agents should not blindly send commands that can change device state. Confirm the exact port, baud rate, payload, encoding, and newline policy before sending commands that may reset, erase, flash, move hardware, change voltage/current, or alter persistent settings.

## Repository Layout

```text
using-serial-devices/
|-- SKILL.md
|-- README.md
|-- agents/
|   `-- openai.yaml
|-- references/
|   |-- agent-workflows.md
|   `-- serial-platforms.md
`-- scripts/
    `-- serial_tool.py
```

## License

Apache License 2.0. See the `LICENSE` file when publishing or packaging this skill.
