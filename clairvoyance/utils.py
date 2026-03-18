import argparse
import io
import json
import logging
import os
import sys
import time
from os import getenv
from typing import Any, Iterable, List

from rich.progress import track as rich_track


class FlushingStreamHandler(logging.StreamHandler):
    """StreamHandler that writes directly to the fd for real-time output.

    Python's logging StreamHandler buffers output when stderr is a pipe
    (non-TTY). Even calling stream.flush() only flushes Python's buffer,
    not the kernel pipe buffer. This subclass writes formatted log lines
    directly via os.write() to bypass all Python-level buffering.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            data = (msg + self.terminator).encode()
            try:
                fd = self.stream.fileno()
                os.write(fd, data)
            except (io.UnsupportedOperation, OSError, AttributeError):
                self.stream.write(msg + self.terminator)
                self.stream.flush()
        except Exception:
            self.handleError(record)


class JsonLogFormatter(logging.Formatter):
    """Emits one JSON object per line for agent-friendly consumption."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if hasattr(record, "event_data"):
            entry.update(record.event_data)
        return json.dumps(entry)


class ProgressTracker:
    """Wall-clock progress tracker with ETA estimation.

    Call advance() after each unit of work completes. Emits an INFO log
    at most every `interval` seconds with rate and time remaining.
    """

    def __init__(
        self,
        total: int,
        phase: str,
        logger: logging.Logger,
        interval: float = 30.0,
    ) -> None:
        self._total = total
        self._phase = phase
        self._logger = logger
        self._interval = interval
        self._completed = 0
        self._start = time.monotonic()
        self._last_report = 0.0

    def advance(self, n: int = 1) -> None:
        self._completed += n
        now = time.monotonic()
        if now - self._last_report >= self._interval:
            self._report(now)
            self._last_report = now

    def finish(self) -> None:
        elapsed = time.monotonic() - self._start
        self._logger.info(
            f"{self._phase}: done ({self._completed} items "
            f"in {_format_duration(elapsed)})"
        )

    def _report(self, now: float) -> None:
        elapsed = now - self._start
        rate = self._completed / elapsed if elapsed > 0 else 0
        remaining = self._total - self._completed
        eta = remaining / rate if rate > 0 else 0
        self._logger.info(
            f"{self._phase}: {self._completed}/{self._total} "
            f"({rate:.1f}/s, ~{_format_duration(eta)} remaining)"
        )

    @property
    def completed(self) -> int:
        return self._completed

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    @property
    def eta(self) -> float:
        elapsed = self.elapsed
        rate = self._completed / elapsed if elapsed > 0 else 0
        remaining = self._total - self._completed
        return remaining / rate if rate > 0 else 0


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m{int(s)}s"
    h, remainder = divmod(seconds, 3600)
    m, _ = divmod(remainder, 60)
    return f"{int(h)}h{int(m)}m"


class Tracker:
    __enabled = False

    @classmethod
    def enable(cls) -> None:
        cls.__enabled = True

    @classmethod
    def disable(cls) -> None:
        cls.__enabled = False

    @classmethod
    def track(cls, it: Iterable, description: str, **kwargs) -> Iterable:  # type: ignore[no-untyped-def]
        if not cls.__enabled:
            return it
        description = f"{description: <32}"
        return rich_track(it, description, **kwargs)


track = Tracker.track


def default(arg: Any, default_value: Any) -> Any:
    return arg if arg is not None else default_value


def set_slow_config(args: argparse.Namespace) -> None:
    args.concurrent_requests = default(args.concurrent_requests, 1)
    args.max_retries = default(args.max_retries, 50)
    args.backoff = default(args.backoff, 2)


def parse_args(args: List[str]) -> argparse.Namespace:
    default_values = {"document": "query { FUZZ }"}

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        default=0,
        action="count",
    )
    parser.add_argument(
        "-i",
        "--input-schema",
        metavar="<file>",
        help="Input file containing JSON schema which will be supplemented with obtained information",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="<file>",
        help="Output file containing JSON schema (default to stdout)",
    )
    parser.add_argument(
        "-d",
        "--document",
        metavar="<string>",
        default=default_values["document"],
        help=f'Start with this document (default {default_values["document"]})',
    )
    parser.add_argument(
        "-H",
        "--header",
        metavar="<header>",
        dest="headers",
        action="append",
        default=[],
    )
    parser.add_argument(
        "-c",
        "--concurrent-requests",
        metavar="<int>",
        type=int,
        default=None,
        help="Number of concurrent requests to send to the server",
    )
    parser.add_argument(
        "-w",
        "--wordlist",
        metavar="<file>",
        type=argparse.FileType("r"),
        help="This wordlist will be used for all brute force effots (fields, arguments and so on)",
    )
    parser.add_argument(
        "-wv",
        "--validate",
        action="store_true",
        help="Validate the wordlist items match name Regex",
    )
    parser.add_argument(
        "-x",
        "--proxy",
        metavar="<string>",
        type=str,
        help="Define a proxy to use for all requests. For more info, read https://docs.aiohttp.org/en/stable/client_advanced.html?highlight=proxy",
    )
    parser.add_argument(
        "-k",
        "--no-ssl",
        action="store_true",
        help="Disable SSL verification",
    )
    parser.add_argument(
        "-m",
        "--max-retries",
        metavar="<int>",
        type=int,
        help="How many retries should be made when a request fails",
    )
    parser.add_argument(
        "-b",
        "--backoff",
        metavar="<int>",
        type=int,
        help="Exponential backoff factor. Delay will be calculated as: `0.5 * backoff**retries` seconds.",
    )
    parser.add_argument(
        "-p",
        "--profile",
        choices=["slow", "fast"],
        default="fast",
        help="Select a speed profile. fast mod will set lot of workers to provide you quick result"
        + " but if the server as some rate limit you may want to use slow mod.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Enable progress bar",
    )
    parser.add_argument(
        "--checkpoint",
        metavar="<file>",
        help="Checkpoint file for resumable scans. Resumes if file exists, otherwise starts fresh.",
    )
    parser.add_argument(
        "--json-log",
        action="store_true",
        help="Emit one JSON object per log line for agent-friendly consumption",
    )
    parser.add_argument(
        "--rate-limit",
        metavar="<float>",
        type=float,
        help="Max requests per second (e.g. 5 = 5 req/s). Paces requests to avoid WAF/rate-limit blocks.",
    )
    parser.add_argument(
        "--no-cookies",
        action="store_true",
        help="Disable cookie jar (cookies are persisted across requests by default)",
    )
    parser.add_argument("url")

    parsed_args = parser.parse_args(args)

    if parsed_args.checkpoint and parsed_args.input_schema:
        parser.error("--checkpoint and -i/--input-schema are mutually exclusive")

    if parsed_args.profile == "slow":
        set_slow_config(parsed_args)

    if parsed_args.progress:
        Tracker.enable()

    return parsed_args


def _force_unbuffered_stderr() -> None:
    """Replace sys.stderr with an unbuffered wrapper at the fd level.

    When stderr is a pipe (e.g. captured by a subprocess), Python defaults
    to block-buffered I/O. This forces write-through mode so that every
    write is immediately visible to the parent process.
    """
    if hasattr(sys.stderr, "fileno"):
        try:
            fd = os.dup(sys.stderr.fileno())
            sys.stderr = io.TextIOWrapper(
                os.fdopen(fd, "wb", buffering=0),
                write_through=True,
            )
        except (io.UnsupportedOperation, OSError):
            pass


def setup_logger(verbosity: int, json_log: bool = False) -> None:
    _force_unbuffered_stderr()

    datefmt = getenv("LOG_DATEFMT") or "%Y-%m-%d %H:%M:%S"

    default_level = getenv("LOG_LEVEL") or "INFO"
    level = "DEBUG" if verbosity >= 1 else default_level.upper()

    if json_log:
        handler = FlushingStreamHandler()
        handler.setFormatter(JsonLogFormatter(datefmt=datefmt))
        logging.root.addHandler(handler)
        logging.root.setLevel(level)
    else:
        fmt = getenv("LOG_FMT") or "%(asctime)s \t%(levelname)s\t| %(message)s"
        handler = FlushingStreamHandler()
        handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
        logging.root.addHandler(handler)
        logging.root.setLevel(level)

    logging.getLogger("asyncio").setLevel(logging.ERROR)
