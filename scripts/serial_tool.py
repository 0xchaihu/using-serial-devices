#!/usr/bin/env python3
"""Agent-friendly serial/TTY/COM helper built on pyserial."""
from __future__ import annotations

import argparse
import codecs
import datetime as _dt
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except Exception as exc:  # Keep import failures machine-readable.
    serial = None
    list_ports = None
    SERIAL_IMPORT_ERROR = exc
else:
    SERIAL_IMPORT_ERROR = None

EXIT_ARG = 1
EXIT_PORT = 2
EXIT_TIMEOUT = 3
EXIT_IO = 4
EXIT_DEP = 5
SESSION_ROOT = Path(tempfile.gettempdir()) / "using-serial-devices"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MATCH_BUFFER_LIMIT = 65536


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def emit(obj, json_mode=True):
    if json_mode:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), flush=True)
    else:
        print(obj, flush=True)


def result(command, ok=True, data=None, error=None):
    out = {"ok": ok, "command": command}
    if data is not None:
        out["data"] = data
    if error is not None:
        out["error"] = error
    return out


def err_obj(exc_type, message, suggestion=""):
    return {"type": exc_type, "message": str(message), "suggestion": suggestion}


def require_pyserial(command, json_mode=True):
    if serial is not None:
        return True
    emit(result(command, False, error=err_obj(
        "MissingDependency",
        f"pyserial is not available: {SERIAL_IMPORT_ERROR}",
        "Install with: python -m pip install pyserial"
    )), json_mode)
    return False


def parse_hex(s):
    text = (s or "").strip()
    if not text:
        return b""
    if re.fullmatch(r"[0-9A-Fa-f]+", text):
        if len(text) % 2:
            raise ValueError("hex input must contain an even number of hex digits")
        return bytes.fromhex(text)
    tokens = re.split(r"[\s,;:-]+", text)
    if any(not re.fullmatch(r"[0-9A-Fa-f]{2}", token or "") for token in tokens):
        raise ValueError("hex input must be pairs of hex digits, for example: 01 02 0A")
    return bytes.fromhex("".join(tokens))


def decode_bytes(data, encoding):
    if encoding == "hex":
        return data.hex(" ")
    return data.decode(encoding, errors="replace")


def append_newline(payload, newline):
    suffix = {"none": b"", "lf": b"\n", "crlf": b"\r\n", "cr": b"\r"}[newline]
    return payload + suffix


def open_serial(args):
    return serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=args.bytesize,
        parity=args.parity,
        stopbits=args.stopbits,
        timeout=args.timeout,
        write_timeout=args.timeout,
        rtscts=args.rtscts,
        dsrdtr=args.dsrdtr,
    )


def cmd_list(args):
    if not require_pyserial("list", args.json):
        return EXIT_DEP
    ports = []
    for p in list_ports.comports():
        ports.append({
            "port": p.device,
            "name": p.name,
            "description": p.description,
            "hwid": p.hwid,
            "vid": p.vid,
            "pid": p.pid,
            "serial_number": p.serial_number,
            "manufacturer": p.manufacturer,
            "product": p.product,
            "interface": p.interface,
        })
    emit(result("list", True, {"ports": ports, "count": len(ports)}), args.json)
    return 0


def cmd_probe(args):
    if not require_pyserial("probe", args.json):
        return EXIT_DEP
    start = time.time()
    try:
        with open_serial(args) as ser:
            data = {"port": args.port, "baud": args.baud, "is_open": ser.is_open, "duration_ms": int((time.time() - start) * 1000)}
        emit(result("probe", True, data), args.json)
        return 0
    except serial.SerialException as exc:
        emit(result("probe", False, error=err_obj("SerialException", exc, "Check the port name, permissions, and whether another program is using it.")), args.json)
        return EXIT_PORT


def read_loop(args, ser, command="read", event_writer=None):
    started = time.time()
    last_data = started
    total = 0
    lines = 0
    chunks = []
    all_bytes = bytearray()
    text_acc = ""
    match_buf = ""
    line_buf = ""
    pattern = re.compile(args.expect) if args.expect else None
    matched = None
    reason = "completed"
    raw_log = open(args.log, "ab") if args.log else None
    retain_history = event_writer is None and not args.jsonl

    def write_event(ev):
        if event_writer:
            event_writer(ev)
        elif args.jsonl:
            emit(ev, True)

    def emit_line(line):
        nonlocal lines, matched
        lines += 1
        line_ev = {"event": "line", "line": line, "time": now_iso()}
        write_event(line_ev)
        if pattern and pattern.search(line):
            matched = line
            write_event({"event": "match", "pattern": args.expect, "line": line, "time": now_iso()})

    try:
        write_event({"event": "open", "port": args.port, "baud": args.baud, "time": now_iso()})
        while True:
            data = ser.read(max(1, min(args.chunk_size, ser.in_waiting or args.chunk_size)))
            current = time.time()
            if data:
                last_data = current
                total += len(data)
                if retain_history:
                    all_bytes.extend(data)
                if raw_log:
                    raw_log.write(data)
                    raw_log.flush()
                text = decode_bytes(data, args.encoding)
                if retain_history:
                    text_acc += text
                    chunks.append({"time": now_iso(), "bytes": len(data), "text": text, "hex": data.hex(" ")})
                ev = {"event": "data", "bytes": len(data), "text": text, "hex": data.hex(" "), "time": now_iso()}
                write_event(ev)

                line_buf += text
                parts = line_buf.splitlines(keepends=True)
                if parts and not (parts[-1].endswith("\n") or parts[-1].endswith("\r")):
                    line_buf = parts.pop()
                else:
                    line_buf = ""
                for part in parts:
                    emit_line(part.rstrip("\r\n"))

                if pattern and not matched:
                    match_buf = (match_buf + text)[-MATCH_BUFFER_LIMIT:]
                    m = pattern.search(text_acc if retain_history else match_buf)
                    if m:
                        matched = m.group(0)
                        write_event({"event": "match", "pattern": args.expect, "text": matched, "time": now_iso()})
                if args.max_bytes and total >= args.max_bytes:
                    reason = "max_bytes"
                    break
                if args.max_lines and lines >= args.max_lines:
                    reason = "max_lines"
                    break
                if matched:
                    reason = "matched"
                    break
            if args.seconds is not None and current - started >= args.seconds:
                reason = "seconds"
                break
            if args.until_idle is not None and current - last_data >= args.until_idle:
                reason = "idle"
                break
            if not args.forever and args.seconds is None and args.until_idle is None and not args.max_bytes and not args.max_lines and not args.expect:
                reason = "no_condition"
                break
    finally:
        if raw_log:
            raw_log.close()
    duration = int((time.time() - started) * 1000)
    data = {"port": args.port, "baud": args.baud, "received_bytes": total, "lines": lines, "partial_line": line_buf, "text": text_acc, "hex": bytes(all_bytes).hex(" "), "matched": matched is not None, "match": matched, "reason": reason, "duration_ms": duration, "chunks": chunks}
    if line_buf:
        write_event({"event": "partial", "text": line_buf, "time": now_iso()})
    write_event({"event": "close", "reason": reason, "received_bytes": total, "time": now_iso()})
    return data

def cmd_read(args):
    if not require_pyserial("read", args.json):
        return EXIT_DEP
    try:
        with open_serial(args) as ser:
            data = read_loop(args, ser, "read")
        if args.expect and not data.get("matched"):
            if not args.jsonl:
                emit(result("read", False, data=data, error=err_obj("Timeout", f"expected pattern not matched: {args.expect}", "Check baud rate/device output or increase wait conditions.")), args.json)
            return EXIT_TIMEOUT
        if not args.jsonl:
            emit(result("read", True, data), args.json)
        return 0
    except serial.SerialException as exc:
        emit(result("read", False, error=err_obj("SerialException", exc, "Check the port, permissions, cable, and baud rate.")), args.json)
        return EXIT_IO


def build_payload(args):
    has_text = args.text is not None
    has_hex = args.hex is not None
    if has_text == has_hex:
        raise ValueError("provide either --text or --hex, but not both")
    if has_hex:
        return parse_hex(args.hex)
    return append_newline(args.text.encode(args.encoding if args.encoding != "hex" else "utf-8"), args.newline)


def cmd_write(args):
    if not require_pyserial("write", args.json):
        return EXIT_DEP
    try:
        payload = build_payload(args)
        if len(payload) == 0:
            emit(result("write", False, error=err_obj("ValueError", "payload is empty (0 bytes)", "Provide --text or --hex with non-empty content.")), args.json)
            return EXIT_ARG
        with open_serial(args) as ser:
            written = ser.write(payload)
            ser.flush()
        emit(result("write", True, {"port": args.port, "baud": args.baud, "sent_bytes": written, "hex": payload.hex(" ")}), args.json)
        return 0
    except ValueError as exc:
        emit(result("write", False, error=err_obj("ValueError", exc, "Use --text TEXT or --hex '01 02 0a'.")), args.json)
        return EXIT_ARG
    except serial.SerialException as exc:
        emit(result("write", False, error=err_obj("SerialException", exc, "Check the port and whether the device accepts writes.")), args.json)
        return EXIT_IO


def cmd_request(args):
    if not require_pyserial("request", args.json):
        return EXIT_DEP
    try:
        payload = build_payload(args)
        if len(payload) == 0:
            emit(result("request", False, error=err_obj("ValueError", "payload is empty (0 bytes)", "Provide --text or --hex with non-empty content.")), args.json)
            return EXIT_ARG
        with open_serial(args) as ser:
            started = time.time()
            sent = ser.write(payload)
            ser.flush()
            data = read_loop(args, ser, "request")
            data.update({"sent_bytes": sent, "sent_hex": payload.hex(" "), "duration_ms": int((time.time() - started) * 1000)})
        if args.expect and not data.get("matched"):
            emit(result("request", False, data=data, error=err_obj("Timeout", f"expected pattern not matched: {args.expect}", "Check baud rate/device output or increase wait conditions.")), args.json)
            return EXIT_TIMEOUT
        emit(result("request", True, data), args.json)
        return 0
    except ValueError as exc:
        emit(result("request", False, error=err_obj("ValueError", exc, "Use --text TEXT or --hex '01 02 0a'.")), args.json)
        return EXIT_ARG
    except serial.SerialException as exc:
        emit(result("request", False, error=err_obj("SerialException", exc, "Check the port, permissions, cable, and baud rate.")), args.json)
        return EXIT_IO


def validate_session_id(session_id):
    if not SESSION_ID_RE.fullmatch(session_id or ""):
        raise ValueError("session must match ^[A-Za-z0-9_.-]{1,64}$")
    if session_id in (".", ".."):
        raise ValueError("session must not be . or ..")


def session_paths(session_id):
    validate_session_id(session_id)
    base = SESSION_ROOT.resolve()
    root = (base / session_id).resolve()
    if base not in root.parents:
        raise ValueError("session path must stay under the using-serial-devices temp directory")
    return root, root / "session.json", root / "events.jsonl", root / "raw.log"


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_monitor_start(args):
    if not require_pyserial("monitor start", args.json):
        return EXIT_DEP
    session_id = args.session or f"serial-{uuid.uuid4().hex[:12]}"
    try:
        root, session_file, event_log, raw_log = session_paths(session_id)
    except ValueError as exc:
        emit(result("monitor start", False, error=err_obj("ValueError", exc, "Use only letters, digits, underscore, dash, and dot in session names.")), args.json)
        return EXIT_ARG
    if root.exists() and any(root.iterdir()):
        emit(result("monitor start", False, data={"session_id": session_id}, error=err_obj("ExistingSession", session_id, "Choose a new --session value or stop/poll the existing session.")), args.json)
        return EXIT_ARG
    root.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "_monitor_worker", "--session", session_id, "--port", args.port, "--baud", str(args.baud), "--bytesize", str(args.bytesize), "--parity", args.parity, "--stopbits", str(args.stopbits), "--timeout", str(args.timeout), "--encoding", args.encoding, "--chunk-size", str(args.chunk_size)]
    if args.rtscts:
        cmd.append("--rtscts")
    if args.dsrdtr:
        cmd.append("--dsrdtr")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": str(root)}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    meta = {"session_id": session_id, "pid": proc.pid, "port": args.port, "baud": args.baud, "event_log": str(event_log), "raw_log": str(raw_log), "started_at": now_iso()}
    write_json(session_file, meta)

    deadline = time.time() + args.startup_timeout
    events = []
    offset = 0
    while time.time() <= deadline:
        events, offset = iter_events(event_log, 0)
        if events:
            first_error = next((e for e in events if e.get("event") == "error"), None)
            if first_error:
                emit(result("monitor start", False, data={**meta, "events": events, "next_offset": offset}, error=first_error.get("error")), args.json)
                return EXIT_PORT
            if any(e.get("event") == "open" for e in events):
                emit(result("monitor start", True, {**meta, "events": events, "next_offset": offset}), args.json)
                return 0
        if proc.poll() is not None:
            break
        time.sleep(0.05)

    if proc.poll() is not None:
        events, offset = iter_events(event_log, 0)
        emit(result("monitor start", False, data={**meta, "events": events, "next_offset": offset}, error=err_obj("WorkerExited", f"monitor worker exited with code {proc.returncode}", "Check the port, permissions, cable, and baud rate.")), args.json)
        return EXIT_IO
    emit(result("monitor start", True, {**meta, "events": events, "next_offset": offset, "warning": "worker is alive but no open event was observed before startup timeout"}), args.json)
    return 0

def iter_events(event_log, since):
    if not event_log.exists():
        return [], since or 0
    events = []
    with event_log.open("rb") as f:
        f.seek(since or 0)
        for line in f:
            try:
                events.append(json.loads(line.decode("utf-8")))
            except json.JSONDecodeError:
                pass
        offset = f.tell()
    return events, offset


def cmd_monitor_poll(args):
    try:
        root, session_file, event_log, raw_log = session_paths(args.session)
    except ValueError as exc:
        emit(result("monitor poll", False, error=err_obj("ValueError", exc, "Use the session_id returned by monitor start.")), args.json)
        return EXIT_ARG
    if not session_file.exists():
        emit(result("monitor poll", False, error=err_obj("UnknownSession", args.session, "Check the session_id from monitor start.")), args.json)
        return EXIT_ARG
    events, offset = iter_events(event_log, args.since)
    emit(result("monitor poll", True, {"session_id": args.session, "events": events, "count": len(events), "has_new_data": any(e.get("event") in ("data", "line", "match") for e in events), "next_offset": offset}), args.json)
    return 0


def cmd_monitor_wait(args):
    try:
        root, session_file, event_log, raw_log = session_paths(args.session)
    except ValueError as exc:
        emit(result("monitor wait", False, error=err_obj("ValueError", exc, "Use the session_id returned by monitor start.")), args.json)
        return EXIT_ARG
    if not session_file.exists():
        emit(result("monitor wait", False, error=err_obj("UnknownSession", args.session, "Check the session_id from monitor start.")), args.json)
        return EXIT_ARG
    deadline = time.time() + args.wait_timeout
    offset = args.since or 0
    pattern = re.compile(args.expect) if args.expect else None
    collected = []
    matched = None
    while time.time() <= deadline:
        events, offset = iter_events(event_log, offset)
        if events:
            collected.extend(events)
            if pattern:
                for ev in events:
                    hay = ev.get("line") or ev.get("text") or ""
                    m = pattern.search(hay)
                    if m:
                        matched = m.group(0)
                        break
                if matched:
                    break
            else:
                break
        time.sleep(args.interval)
    ok = (matched is not None) if pattern else bool(collected)
    data = {"session_id": args.session, "events": collected, "count": len(collected), "matched": matched is not None, "match": matched, "next_offset": offset}
    if ok:
        emit(result("monitor wait", True, data), args.json)
        return 0
    emit(result("monitor wait", False, data=data, error=err_obj("Timeout", "no matching event before timeout" if pattern else "no new event before timeout", "Increase --timeout, poll from an earlier offset, or check monitor status.")), args.json)
    return EXIT_TIMEOUT


def pid_alive(pid):
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
            return str(pid) in r.stdout
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def cmd_monitor_status(args):
    try:
        root, session_file, event_log, raw_log = session_paths(args.session)
    except ValueError as exc:
        emit(result("monitor status", False, error=err_obj("ValueError", exc, "Use the session_id returned by monitor start.")), args.json)
        return EXIT_ARG
    if not session_file.exists():
        emit(result("monitor status", False, error=err_obj("UnknownSession", args.session, "Check the session_id from monitor start.")), args.json)
        return EXIT_ARG
    meta = read_json(session_file)
    events, offset = iter_events(event_log, 0)
    meta.update({"alive": pid_alive(meta.get("pid")), "event_count": len(events), "event_log_size": offset, "raw_log_size": raw_log.stat().st_size if raw_log.exists() else 0, "last_event": events[-1] if events else None})
    emit(result("monitor status", True, meta), args.json)
    return 0


def cmd_monitor_stop(args):
    try:
        root, session_file, event_log, raw_log = session_paths(args.session)
    except ValueError as exc:
        emit(result("monitor stop", False, error=err_obj("ValueError", exc, "Use the session_id returned by monitor start.")), args.json)
        return EXIT_ARG
    if not session_file.exists():
        emit(result("monitor stop", False, error=err_obj("UnknownSession", args.session, "Check the session_id from monitor start.")), args.json)
        return EXIT_ARG
    meta = read_json(session_file)
    pid = meta.get("pid")
    stopped = False
    if pid and pid_alive(pid):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                if pid_alive(pid):
                    os.kill(pid, signal.SIGKILL)
            stopped = True
        except Exception:
            stopped = False
    events, offset = iter_events(event_log, 0)
    emit(result("monitor stop", True, {"session_id": args.session, "pid": pid, "stopped": stopped, "event_count": len(events), "event_log": str(event_log), "raw_log": str(raw_log)}), args.json)
    return 0


def cmd_monitor_worker(args):
    if not require_pyserial("_monitor_worker", True):
        return EXIT_DEP
    root, session_file, event_log, raw_log = session_paths(args.session)
    root.mkdir(parents=True, exist_ok=True)
    def event_writer(ev):
        ev.setdefault("session_id", args.session)
        with event_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
    args.log = str(raw_log)
    args.jsonl = False
    args.json = True
    args.forever = True
    args.seconds = None
    args.until_idle = None
    args.max_bytes = None
    args.max_lines = None
    args.expect = None
    try:
        with open_serial(args) as ser:
            read_loop(args, ser, "monitor", event_writer)
        return 0
    except Exception as exc:
        event_writer({"event": "error", "error": err_obj(type(exc).__name__, exc, "Check serial settings and device connection."), "time": now_iso()})
        return EXIT_IO


def cmd_terminal(args):
    if not require_pyserial("terminal", getattr(args, "json", True)):
        return EXIT_DEP
    emit("Starting serial terminal. Type :exit or press Ctrl+C to quit.", False)
    stop = threading.Event()
    raw_log = open(args.log, "ab") if args.log else None

    def reader(ser):
        while not stop.is_set():
            try:
                data = ser.read(max(1, ser.in_waiting or 1))
            except serial.SerialException as exc:
                emit(result("terminal", False, error=err_obj("SerialException", exc, "Serial read failed.")), True)
                stop.set()
                break
            if data:
                if raw_log:
                    raw_log.write(data)
                    raw_log.flush()
                sys.stdout.write(decode_bytes(data, args.encoding))
                sys.stdout.flush()

    try:
        with open_serial(args) as ser:
            t = threading.Thread(target=reader, args=(ser,), daemon=True)
            t.start()
            for line in sys.stdin:
                if line.rstrip("\r\n") in (":exit", ":quit"):
                    break
                payload = line.encode(args.encoding if args.encoding != "hex" else "utf-8")
                ser.write(payload)
                ser.flush()
            stop.set()
            t.join(timeout=1)
        return 0
    except KeyboardInterrupt:
        stop.set()
        return 0
    except serial.SerialException as exc:
        emit(result("terminal", False, error=err_obj("SerialException", exc, "Check the port and permissions.")), True)
        return EXIT_IO
    finally:
        if raw_log:
            raw_log.close()

def add_serial_args(p):
    p.add_argument("--port", required=True)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--bytesize", type=int, default=8)
    p.add_argument("--parity", default="N", choices=["N", "E", "O", "M", "S"])
    p.add_argument("--stopbits", type=float, default=1)
    p.add_argument("--timeout", type=float, default=1.0)
    p.add_argument("--rtscts", action="store_true")
    p.add_argument("--dsrdtr", action="store_true")


def add_output_args(p):
    p.add_argument("--json", action="store_true", default=True)


def add_read_args(p):
    p.add_argument("--seconds", type=float)
    p.add_argument("--until-idle", type=float)
    p.add_argument("--max-bytes", type=int)
    p.add_argument("--max-lines", type=int)
    p.add_argument("--expect")
    p.add_argument("--forever", action="store_true")
    p.add_argument("--jsonl", action="store_true")
    p.add_argument("--log")
    p.add_argument("--encoding", default="utf-8", choices=["utf-8", "latin-1", "ascii", "hex"])
    p.add_argument("--chunk-size", type=int, default=256)


def build_parser():
    parser = argparse.ArgumentParser(description="Agent-friendly serial/TTY/COM tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    add_output_args(p); p.set_defaults(func=cmd_list)

    p = sub.add_parser("probe")
    add_serial_args(p); add_output_args(p); p.set_defaults(func=cmd_probe)

    p = sub.add_parser("read")
    add_serial_args(p); add_output_args(p); add_read_args(p); p.set_defaults(func=cmd_read)

    p = sub.add_parser("write")
    add_serial_args(p); add_output_args(p)
    p.add_argument("--text"); p.add_argument("--hex")
    p.add_argument("--newline", default="none", choices=["none", "lf", "crlf", "cr"]); p.add_argument("--encoding", default="utf-8", choices=["utf-8", "latin-1", "ascii", "hex"])
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("request")
    add_serial_args(p); add_output_args(p); add_read_args(p)
    p.add_argument("--text"); p.add_argument("--hex")
    p.add_argument("--newline", default="lf", choices=["none", "lf", "crlf", "cr"])
    p.set_defaults(func=cmd_request)

    p = sub.add_parser("terminal")
    add_serial_args(p); add_output_args(p); p.add_argument("--encoding", default="utf-8", choices=["utf-8", "latin-1", "ascii", "hex"]); p.add_argument("--log")
    p.set_defaults(func=cmd_terminal)

    mon = sub.add_parser("monitor")
    mon_sub = mon.add_subparsers(dest="monitor_cmd", required=True)
    p = mon_sub.add_parser("start")
    add_serial_args(p); add_output_args(p); p.add_argument("--session"); p.add_argument("--encoding", default="utf-8", choices=["utf-8", "latin-1", "ascii", "hex"]); p.add_argument("--chunk-size", type=int, default=256); p.add_argument("--startup-timeout", type=float, default=2.0); p.set_defaults(func=cmd_monitor_start)
    p = mon_sub.add_parser("poll")
    add_output_args(p); p.add_argument("--session", required=True); p.add_argument("--since", type=int, default=0); p.set_defaults(func=cmd_monitor_poll)
    p = mon_sub.add_parser("wait")
    add_output_args(p); p.add_argument("--session", required=True); p.add_argument("--since", type=int, default=0); p.add_argument("--expect"); p.add_argument("--timeout", dest="wait_timeout", type=float, default=30); p.add_argument("--interval", type=float, default=0.2); p.set_defaults(func=cmd_monitor_wait)
    p = mon_sub.add_parser("status")
    add_output_args(p); p.add_argument("--session", required=True); p.set_defaults(func=cmd_monitor_status)
    p = mon_sub.add_parser("stop")
    add_output_args(p); p.add_argument("--session", required=True); p.set_defaults(func=cmd_monitor_stop)

    p = sub.add_parser("_monitor_worker")
    add_serial_args(p); add_read_args(p); p.add_argument("--session", required=True); p.set_defaults(func=cmd_monitor_worker)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 0
    except Exception as exc:
        emit(result(args.cmd, False, error=err_obj(type(exc).__name__, exc, "Run with --help and verify arguments.")), True)
        return EXIT_IO


if __name__ == "__main__":
    raise SystemExit(main())
