# Serial Platform Notes

## Windows

Ports are usually `COM3`, `COM4`, etc. Pyserial handles `COM10` and above; pass the visible port name returned by `list`. If a port fails to open, another program such as a terminal, IDE, or debugger may already own it.

## Linux

Common device names include `/dev/ttyUSB0`, `/dev/ttyACM0`, and board-specific `/dev/serial/by-id/*` symlinks. Permission errors usually mean the user needs membership in `dialout`, `uucp`, or a distro-specific serial group, or needs a temporary permission change.

## macOS

Use `/dev/cu.*` for agent-initiated connections. `/dev/tty.*` may exist for the same adapter but is usually less convenient for outbound serial sessions.

## Parameters

Most embedded boards use 115200 8N1: baud 115200, 8 data bits, no parity, 1 stop bit. Some modems and bootloaders need `crlf` line endings; many MCU shells use `lf` or `cr`. If output is garbled, check baud rate and encoding first.

DTR/RTS can reset some boards. Leave them at defaults unless the task specifically requires hardware flow control or reset behavior.
