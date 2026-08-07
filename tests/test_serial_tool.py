import importlib.util
import json
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "serial_tool.py"
RUNTIME = Path(__file__).parent / "runtime"
SPEC = importlib.util.spec_from_file_location("serial_tool", SCRIPT)
serial_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(serial_tool)


class FakeSerial:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.is_open = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, _size):
        if self.chunks:
            item = self.chunks.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return b""


class SerialToolTests(unittest.TestCase):
    def test_rotating_binary_log_keeps_backups(self):
        path = RUNTIME / "serial.raw.log"
        for candidate in (path, Path(f"{path}.1"), Path(f"{path}.2")):
            candidate.unlink(missing_ok=True)
        log = serial_tool.RotatingBinaryLog(path, max_bytes=5, backups=2)
        log.write(b"abcd")
        log.write(b"efgh")
        log.close()
        self.assertEqual(path.read_bytes(), b"efgh")
        self.assertEqual(Path(f"{path}.1").read_bytes(), b"abcd")
        for candidate in (path, Path(f"{path}.1"), Path(f"{path}.2")):
            candidate.unlink(missing_ok=True)

    def test_read_loop_emits_one_silence_alert(self):
        events = []
        args = types.SimpleNamespace(
            port="FAKE", baud=115200, expect=None, log=None, jsonl=False, encoding="utf-8", chunk_size=32,
            max_bytes=None, max_lines=None, seconds=0.02, until_idle=None,
            forever=False, silence_timeout=0.001,
        )
        serial_tool.read_loop(args, FakeSerial([]), event_writer=events.append)
        alerts = [event for event in events if event.get("kind") == "silence"]
        self.assertEqual(len(alerts), 1)

    def test_read_loop_bounds_partial_line_buffer(self):
        events = []
        old_limit = serial_tool.MATCH_BUFFER_LIMIT
        serial_tool.MATCH_BUFFER_LIMIT = 4
        args = types.SimpleNamespace(
            port="FAKE", baud=115200, expect=None, log=None, jsonl=False,
            encoding="utf-8", chunk_size=32, max_bytes=5, max_lines=None,
            seconds=None, until_idle=None, forever=False, silence_timeout=None,
        )
        try:
            data = serial_tool.read_loop(args, FakeSerial([b"12345"]), event_writer=events.append)
        finally:
            serial_tool.MATCH_BUFFER_LIMIT = old_limit
        self.assertEqual(data["partial_line"], "2345")
        self.assertTrue(any(event.get("event") == "buffer_truncated" for event in events))

    def test_monitor_worker_emits_pattern_alert(self):
        old_root = serial_tool.SESSION_ROOT
        old_open = serial_tool.open_serial
        old_serial = serial_tool.serial
        event_log = RUNTIME / "test-alert" / "events.jsonl"
        raw_log = RUNTIME / "test-alert" / "raw.log"
        event_log.unlink(missing_ok=True)
        raw_log.unlink(missing_ok=True)
        serial_tool.SESSION_ROOT = RUNTIME
        serial_tool.serial = object()
        serial_tool.open_serial = lambda _args: FakeSerial([
            b"boot ok\nERROR exploded\n",
            OSError("device disconnected"),
        ])
        try:
            args = types.SimpleNamespace(
                session="test-alert", port="COM9", baud=115200, bytesize=8,
                parity="N", stopbits=1, timeout=0.01, rtscts=False, dsrdtr=False,
                encoding="utf-8", chunk_size=64, log_path=None,
                log_max_bytes=0, log_backups=2, reconnect=False,
                reconnect_delay=0, reconnect_max_attempts=0,
                alert=["ERROR|panic"], silence_timeout=None,
                alert_cooldown=0, webhook_timeout=0.1,
            )
            self.assertEqual(serial_tool.cmd_monitor_worker(args), serial_tool.EXIT_IO)
            events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            alerts = [event for event in events if event.get("event") == "alert"]
            self.assertTrue(alerts, events)
            self.assertEqual(alerts[0]["match"], "ERROR")
            self.assertEqual(alerts[0]["line"], "ERROR exploded")
        finally:
            serial_tool.SESSION_ROOT = old_root
            serial_tool.open_serial = old_open
            serial_tool.serial = old_serial
            event_log.unlink(missing_ok=True)
            raw_log.unlink(missing_ok=True)

    def test_invalid_alert_regex_is_rejected(self):
        with self.assertRaises(Exception):
            serial_tool.compile_alert_patterns(["["])

    def test_webhook_adds_human_readable_text(self):
        response = mock.MagicMock()
        response.__enter__.return_value.status = 200
        with mock.patch.object(serial_tool.urllib.request, "urlopen", return_value=response) as urlopen:
            status = serial_tool.post_alert_webhook(
                "https://example.com/hook",
                {"event": "alert", "kind": "silence", "silence_seconds": 10},
            )
        self.assertEqual(status, 200)
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertIn("no data for 10 seconds", body["text"])

    def test_monitor_worker_reconnects_and_records_retries(self):
        old_root = serial_tool.SESSION_ROOT
        old_open = serial_tool.open_serial
        old_serial = serial_tool.serial
        session_root = RUNTIME / "test-reconnect"
        event_log = session_root / "events.jsonl"
        raw_log = session_root / "raw.log"
        event_log.unlink(missing_ok=True)
        raw_log.unlink(missing_ok=True)
        opens = [
            OSError("not connected"),
            FakeSerial([b"ready\n", OSError("cable removed")]),
            OSError("still missing"),
        ]

        def fake_open(_args):
            item = opens.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        serial_tool.SESSION_ROOT = RUNTIME
        serial_tool.serial = object()
        serial_tool.open_serial = fake_open
        try:
            args = types.SimpleNamespace(
                session="test-reconnect", port="COM9", baud=115200, bytesize=8,
                parity="N", stopbits=1, timeout=0.01, rtscts=False, dsrdtr=False,
                encoding="utf-8", chunk_size=64, log_path=None,
                log_max_bytes=0, log_backups=2, reconnect=True,
                reconnect_delay=0, reconnect_max_attempts=1,
                alert=[], silence_timeout=None, alert_cooldown=0,
                webhook_timeout=0.1,
            )
            self.assertEqual(serial_tool.cmd_monitor_worker(args), serial_tool.EXIT_IO)
            events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(event.get("event") == "open" for event in events))
            self.assertEqual(sum(event.get("event") == "reconnect_wait" for event in events), 2)
        finally:
            serial_tool.SESSION_ROOT = old_root
            serial_tool.open_serial = old_open
            serial_tool.serial = old_serial
            event_log.unlink(missing_ok=True)
            raw_log.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
