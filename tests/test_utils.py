"""Tests for utils.py: progress tracker, JSON log formatter, CLI argument parsing, and log flushing."""

import io
import json
import logging
import os
import sys
import tempfile
import time
import unittest
from io import StringIO
from unittest.mock import patch

from clairvoyance.utils import (
    FlushingStreamHandler,
    JsonLogFormatter,
    ProgressTracker,
    _force_unbuffered_stderr,
    _format_duration,
    parse_args,
    setup_logger,
)


class TestFormatDuration(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(_format_duration(5), "5s")
        self.assertEqual(_format_duration(0), "0s")
        self.assertEqual(_format_duration(59), "59s")

    def test_minutes(self) -> None:
        self.assertEqual(_format_duration(60), "1m0s")
        self.assertEqual(_format_duration(90), "1m30s")
        self.assertEqual(_format_duration(3599), "59m59s")

    def test_hours(self) -> None:
        self.assertEqual(_format_duration(3600), "1h0m")
        self.assertEqual(_format_duration(5400), "1h30m")


class TestProgressTracker(unittest.TestCase):
    def test_advance_and_properties(self) -> None:
        logger = logging.getLogger("test.progress")
        pt = ProgressTracker(total=10, phase="Test", logger=logger)
        self.assertEqual(pt.completed, 0)

        pt.advance(3)
        self.assertEqual(pt.completed, 3)
        self.assertGreater(pt.elapsed, 0)

    def test_eta_decreases(self) -> None:
        logger = logging.getLogger("test.progress")
        pt = ProgressTracker(total=10, phase="Test", logger=logger)
        pt.advance(5)
        eta_half = pt.eta
        pt.advance(3)
        eta_later = pt.eta
        # ETA should be less after more work is done
        self.assertLessEqual(eta_later, eta_half)

    def test_periodic_report(self) -> None:
        logger = logging.getLogger("test.progress.report")
        logger.setLevel(logging.DEBUG)
        # interval=0 means report on every advance
        pt = ProgressTracker(
            total=5, phase="Test", logger=logger, interval=0
        )
        with self.assertLogs(logger, level="INFO") as cm:
            pt.advance()
            pt.advance()
            pt.finish()

        log_text = "\n".join(cm.output)
        self.assertIn("Test: 1/5", log_text)
        self.assertIn("remaining", log_text)
        self.assertIn("done", log_text)

    def test_finish_reports_total(self) -> None:
        logger = logging.getLogger("test.progress.finish")
        logger.setLevel(logging.DEBUG)
        pt = ProgressTracker(total=3, phase="Scan", logger=logger)
        pt.advance(3)
        with self.assertLogs(logger, level="INFO") as cm:
            pt.finish()
        self.assertIn("done (3 items", cm.output[0])

    def test_no_report_before_interval(self) -> None:
        logger = logging.getLogger("test.progress.quiet")
        logger.setLevel(logging.DEBUG)
        # Large interval — no reports during advance
        pt = ProgressTracker(
            total=100, phase="Quiet", logger=logger, interval=9999
        )
        # advance without triggering a report
        pt.advance()
        pt.advance()
        # Only finish should log
        with self.assertLogs(logger, level="INFO") as cm:
            pt.finish()
        self.assertEqual(len(cm.output), 1)


class TestFlushingStreamHandler(unittest.TestCase):
    def test_emit_to_stringio_fallback(self) -> None:
        """StringIO has no fileno(); handler falls back to stream.write+flush."""
        stream = StringIO()
        handler = FlushingStreamHandler(stream=stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        handler.emit(record)
        self.assertEqual(stream.getvalue(), "hello\n")

    def test_emit_writes_directly_to_fd(self) -> None:
        """When stream has a fileno(), emit uses os.write for real-time output."""
        r_fd, w_fd = os.pipe()
        try:
            w_file = os.fdopen(w_fd, "w")
            handler = FlushingStreamHandler(stream=w_file)
            handler.setFormatter(logging.Formatter("%(message)s"))

            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="pipe-test",
                args=(),
                exc_info=None,
            )
            handler.emit(record)

            # Read from the pipe — data should be available immediately
            # (no buffering delay)
            data = os.read(r_fd, 4096)
            self.assertEqual(data, b"pipe-test\n")
        finally:
            os.close(r_fd)
            # w_fd is owned by w_file; closing w_file closes it
            try:
                w_file.close()
            except OSError:
                pass

    def test_emit_uses_os_write_not_stream_write(self) -> None:
        """Verify os.write is called when fileno() is available."""
        r_fd, w_fd = os.pipe()
        try:
            w_file = os.fdopen(w_fd, "w")
            handler = FlushingStreamHandler(stream=w_file)
            handler.setFormatter(logging.Formatter("%(message)s"))

            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="direct-write",
                args=(),
                exc_info=None,
            )

            with patch("clairvoyance.utils.os.write", wraps=os.write) as mock:
                handler.emit(record)
                mock.assert_called_once()
                args = mock.call_args[0]
                self.assertEqual(args[1], b"direct-write\n")

            os.read(r_fd, 4096)  # drain pipe
        finally:
            os.close(r_fd)
            try:
                w_file.close()
            except OSError:
                pass


class TestForceUnbufferedStderr(unittest.TestCase):
    def test_sets_write_through(self) -> None:
        """After _force_unbuffered_stderr, sys.stderr should be write-through."""
        original = sys.stderr
        try:
            _force_unbuffered_stderr()
            # write_through means the TextIOWrapper doesn't buffer
            if isinstance(sys.stderr, io.TextIOWrapper):
                self.assertTrue(sys.stderr.write_through)
        finally:
            sys.stderr = original

    def test_survives_missing_fileno(self) -> None:
        """_force_unbuffered_stderr handles streams without fileno() gracefully."""
        original = sys.stderr
        try:
            sys.stderr = StringIO()
            # Should not raise
            _force_unbuffered_stderr()
        finally:
            sys.stderr = original


class TestRealtimeLogOutput(unittest.TestCase):
    """Integration test: verify log output is immediately readable from a pipe."""

    def test_log_visible_through_pipe(self) -> None:
        """Simulate a parent process reading log output through a pipe."""
        r_fd, w_fd = os.pipe()
        try:
            w_file = os.fdopen(w_fd, "w")

            logger = logging.getLogger("test.pipe.realtime")
            logger.setLevel(logging.INFO)
            logger.handlers.clear()
            handler = FlushingStreamHandler(stream=w_file)
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)

            logger.info("line-one")
            logger.info("line-two")

            # Read immediately — both lines should be available
            data = os.read(r_fd, 4096).decode()
            self.assertIn("line-one", data)
            self.assertIn("line-two", data)
        finally:
            os.close(r_fd)
            try:
                w_file.close()
            except OSError:
                pass
            logger.handlers.clear()

    def test_json_log_visible_through_pipe(self) -> None:
        """JSON log lines are also immediately visible through a pipe."""
        r_fd, w_fd = os.pipe()
        try:
            w_file = os.fdopen(w_fd, "w")

            logger = logging.getLogger("test.pipe.json")
            logger.setLevel(logging.INFO)
            logger.handlers.clear()
            handler = FlushingStreamHandler(stream=w_file)
            handler.setFormatter(JsonLogFormatter(datefmt="%H:%M:%S"))
            logger.addHandler(handler)

            logger.info("json-test")

            data = os.read(r_fd, 4096).decode()
            parsed = json.loads(data.strip())
            self.assertEqual(parsed["message"], "json-test")
            self.assertEqual(parsed["level"], "INFO")
        finally:
            os.close(r_fd)
            try:
                w_file.close()
            except OSError:
                pass
            logger.handlers.clear()


class TestJsonLogFormatter(unittest.TestCase):
    def test_produces_valid_json(self) -> None:
        formatter = JsonLogFormatter(datefmt="%Y-%m-%d %H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        line = formatter.format(record)
        parsed = json.loads(line)
        self.assertEqual(parsed["level"], "INFO")
        self.assertEqual(parsed["message"], "hello world")
        self.assertIn("timestamp", parsed)

    def test_includes_event_data(self) -> None:
        formatter = JsonLogFormatter(datefmt="%Y-%m-%d %H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="field discovered",
            args=(),
            exc_info=None,
        )
        record.event_data = {"event": "field_discovered", "field": "users"}
        line = formatter.format(record)
        parsed = json.loads(line)
        self.assertEqual(parsed["event"], "field_discovered")
        self.assertEqual(parsed["field"], "users")


class TestParseArgsJsonLog(unittest.TestCase):
    def test_json_log_default_false(self) -> None:
        args = parse_args(["http://example.com/graphql"])
        self.assertFalse(args.json_log)

    def test_json_log_flag(self) -> None:
        args = parse_args(["--json-log", "http://example.com/graphql"])
        self.assertTrue(args.json_log)


class TestParseArgsRateLimit(unittest.TestCase):
    def test_rate_limit_default_none(self) -> None:
        args = parse_args(["http://example.com/graphql"])
        self.assertIsNone(args.rate_limit)

    def test_rate_limit_flag(self) -> None:
        args = parse_args(["--rate-limit", "5", "http://example.com/graphql"])
        self.assertEqual(args.rate_limit, 5.0)

    def test_rate_limit_float(self) -> None:
        args = parse_args(["--rate-limit", "2.5", "http://example.com/graphql"])
        self.assertEqual(args.rate_limit, 2.5)


class TestParseArgsNoCookies(unittest.TestCase):
    def test_no_cookies_default_false(self) -> None:
        args = parse_args(["http://example.com/graphql"])
        self.assertFalse(args.no_cookies)

    def test_no_cookies_flag(self) -> None:
        args = parse_args(["--no-cookies", "http://example.com/graphql"])
        self.assertTrue(args.no_cookies)


if __name__ == "__main__":
    unittest.main()
