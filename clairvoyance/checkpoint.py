"""Save and load session state for resumable scans."""

import json
import os
from typing import NamedTuple, Set

CHECKPOINT_VERSION = 1


class CheckpointState(NamedTuple):
    schema: dict
    ignored: Set[str]
    input_document: str
    iteration: int
    url: str


def save_checkpoint(
    path: str,
    schema: dict,
    ignored: Set[str],
    input_document: str,
    iteration: int,
    url: str,
) -> None:
    """Write checkpoint atomically (write .tmp then rename)."""
    data = {
        "version": CHECKPOINT_VERSION,
        "url": url,
        "schema": schema,
        "ignored": sorted(ignored),
        "input_document": input_document,
        "iteration": iteration,
    }
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def load_checkpoint(path: str) -> CheckpointState:
    """Read and validate a checkpoint file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Checkpoint file is not a JSON object: {path}")

    version = data.get("version")
    if version != CHECKPOINT_VERSION:
        raise ValueError(
            f"Unsupported checkpoint version {version} (expected {CHECKPOINT_VERSION})"
        )

    required_keys = {
        "version",
        "url",
        "schema",
        "ignored",
        "input_document",
        "iteration",
    }
    missing = required_keys - data.keys()
    if missing:
        raise ValueError(f"Checkpoint file missing keys: {', '.join(sorted(missing))}")

    return CheckpointState(
        schema=data["schema"],
        ignored=set(data["ignored"]),
        input_document=data["input_document"],
        iteration=data["iteration"],
        url=data["url"],
    )
