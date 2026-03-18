"""Tests for checkpoint save/load functionality."""

import json
import os

import pytest

from clairvoyance.checkpoint import CHECKPOINT_VERSION, load_checkpoint, save_checkpoint


@pytest.fixture()
def checkpoint_path(tmp_path):
    return str(tmp_path / "test.checkpoint")


def _sample_schema():
    return {
        "data": {
            "__schema": {
                "queryType": {"name": "Query"},
                "mutationType": None,
                "subscriptionType": None,
                "directives": [],
                "types": [],
            }
        }
    }


def _sample_ignored():
    return {"String", "Int", "Boolean", "Float", "ID", "User"}


class TestSaveLoadRoundtrip:
    def test_roundtrip(self, checkpoint_path):
        schema = _sample_schema()
        ignored = _sample_ignored()
        doc = "query { user { FUZZ } }"

        save_checkpoint(
            checkpoint_path, schema, ignored, doc, 5, "http://example.com/graphql"
        )
        state = load_checkpoint(checkpoint_path)

        assert state.schema == schema
        assert state.ignored == ignored
        assert state.input_document == doc
        assert state.iteration == 5
        assert state.url == "http://example.com/graphql"

    def test_ignored_set_roundtrip(self, checkpoint_path):
        """Set serializes to sorted list and deserializes back to set."""
        ignored = {"Zebra", "Alpha", "Middle"}
        save_checkpoint(
            checkpoint_path, _sample_schema(), ignored, "query { FUZZ }", 1, "http://x"
        )

        with open(checkpoint_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["ignored"] == ["Alpha", "Middle", "Zebra"]

        state = load_checkpoint(checkpoint_path)
        assert state.ignored == ignored
        assert isinstance(state.ignored, set)


class TestLoadErrors:
    def test_corrupted_json(self, checkpoint_path):
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        with pytest.raises(json.JSONDecodeError):
            load_checkpoint(checkpoint_path)

    def test_missing_keys(self, checkpoint_path):
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump({"version": CHECKPOINT_VERSION, "url": "http://x"}, f)

        with pytest.raises(ValueError, match="missing keys"):
            load_checkpoint(checkpoint_path)

    def test_unknown_version(self, checkpoint_path):
        data = {
            "version": 999,
            "url": "http://x",
            "schema": {},
            "ignored": [],
            "input_document": "",
            "iteration": 1,
        }
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with pytest.raises(ValueError, match="Unsupported checkpoint version"):
            load_checkpoint(checkpoint_path)


class TestAtomicWrite:
    def test_no_tmp_file_remains(self, checkpoint_path):
        save_checkpoint(
            checkpoint_path, _sample_schema(), set(), "query { FUZZ }", 1, "http://x"
        )

        assert os.path.exists(checkpoint_path)
        assert not os.path.exists(checkpoint_path + ".tmp")
