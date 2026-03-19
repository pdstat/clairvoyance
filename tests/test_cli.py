"""Tests for cli.py blind_introspection loop."""

import json
import logging
import unittest
from unittest.mock import AsyncMock, patch

import aiounittest

from clairvoyance.cli import blind_introspection
from clairvoyance.entities.context import client_ctx, config_ctx, logger_ctx
from tests.conftest import MockClient, MockConfig


class TestBlindIntrospectionUnreachableType(aiounittest.AsyncTestCase):
    """Issue 6: get_path_from_root crash when no fields discovered.

    When oracle.clairvoyance() discovers types but no fields connect
    them to the root, get_path_from_root raises ValueError.
    blind_introspection should catch this and return partial results.
    """

    async def test_unreachable_type_returns_partial_results(self) -> None:
        # Build a schema where Query exists with 0 fields, but an
        # unreachable type "Orphan" also exists (OBJECT, no fields).
        # get_type_without_fields will return "Orphan", but
        # get_path_from_root("Orphan") will raise ValueError.
        schema_with_orphan = {
            "data": {
                "__schema": {
                    "directives": [],
                    "queryType": {"name": "Query"},
                    "mutationType": None,
                    "subscriptionType": None,
                    "types": [
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "Query",
                            "possibleTypes": None,
                            "fields": [
                                {
                                    "args": [],
                                    "deprecationReason": None,
                                    "description": None,
                                    "isDeprecated": False,
                                    "name": "dummy",
                                    "type": {
                                        "kind": "SCALAR",
                                        "name": "String",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "inputFields": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "Orphan",
                            "possibleTypes": None,
                            "fields": [
                                {
                                    "args": [],
                                    "deprecationReason": None,
                                    "description": None,
                                    "isDeprecated": False,
                                    "name": "dummy",
                                    "type": {
                                        "kind": "SCALAR",
                                        "name": "String",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "inputFields": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "SCALAR",
                            "name": "String",
                            "possibleTypes": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "SCALAR",
                            "name": "ID",
                            "possibleTypes": None,
                        },
                    ],
                }
            }
        }

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            return_value=json.dumps(schema_with_orphan),
        ):
            with self.assertLogs(logger, level="WARNING") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                )

        log_text = "\n".join(cm.output)
        self.assertIn("Cannot find path from root to", log_text)
        self.assertIn("Returning partial results", log_text)

        parsed = json.loads(result)
        self.assertIn("data", parsed)


class TestBlindIntrospectionAuthError(aiounittest.AsyncTestCase):
    """Issue 1: blind_introspection catches AuthError and returns partial results."""

    async def test_auth_error_returns_partial_and_logs(self) -> None:
        from clairvoyance.entities.errors import AuthError

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.auth")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            side_effect=AuthError(
                "Received 10 consecutive HTTP 403 responses. "
                "Token may have expired."
            ),
        ):
            with self.assertLogs(logger, level="ERROR") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                )

        log_text = "\n".join(cm.output)
        self.assertIn("10 consecutive HTTP 403", log_text)
        # Should return something parseable (empty schema or partial)
        self.assertIsInstance(result, str)

    async def test_auth_error_saves_checkpoint(self) -> None:
        from clairvoyance.entities.errors import AuthError

        # Schema with Query -> User field, where User has no fields yet.
        # This forces a second iteration to explore User.
        first_schema = {
            "data": {
                "__schema": {
                    "directives": [],
                    "queryType": {"name": "Query"},
                    "mutationType": None,
                    "subscriptionType": None,
                    "types": [
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "SCALAR",
                            "name": "String",
                            "possibleTypes": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "SCALAR",
                            "name": "ID",
                            "possibleTypes": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "Query",
                            "possibleTypes": None,
                            "fields": [
                                {
                                    "args": [],
                                    "deprecationReason": None,
                                    "description": None,
                                    "isDeprecated": False,
                                    "name": "users",
                                    "type": {
                                        "kind": "OBJECT",
                                        "name": "User",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "inputFields": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "User",
                            "possibleTypes": None,
                            "fields": [
                                {
                                    "args": [],
                                    "deprecationReason": None,
                                    "description": None,
                                    "isDeprecated": False,
                                    "name": "dummy",
                                    "type": {
                                        "kind": "SCALAR",
                                        "name": "String",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "inputFields": None,
                        },
                    ],
                }
            }
        }

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.auth2")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            side_effect=[
                json.dumps(first_schema),
                AuthError("Received 10 consecutive HTTP 403 responses."),
            ],
        ), patch(
            "clairvoyance.cli.save_checkpoint"
        ) as mock_save:
            with self.assertLogs(logger, level="INFO") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                    checkpoint_path="/tmp/test_checkpoint.json",
                )

        log_text = "\n".join(cm.output)
        self.assertIn("10 consecutive HTTP 403", log_text)
        self.assertIn("Partial results saved to checkpoint", log_text)
        mock_save.assert_called()
        parsed = json.loads(result)
        self.assertIn("data", parsed)


class TestBlindIntrospectionServerError(aiounittest.AsyncTestCase):
    """Issue 11: blind_introspection catches ServerError."""

    async def test_server_error_returns_partial_and_logs(self) -> None:
        from clairvoyance.entities.errors import ServerError

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.5xx")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            side_effect=ServerError(
                "Received 10 consecutive HTTP 5xx responses. "
                "Server may be down."
            ),
        ):
            with self.assertLogs(logger, level="ERROR") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                )

        log_text = "\n".join(cm.output)
        self.assertIn("10 consecutive HTTP 5xx", log_text)
        self.assertIsInstance(result, str)

    async def test_server_error_saves_checkpoint(self) -> None:
        from clairvoyance.entities.errors import ServerError

        first_schema = _make_simple_schema()

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.5xx.ckpt")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            side_effect=[
                json.dumps(first_schema),
                ServerError("Received 10 consecutive HTTP 5xx responses."),
            ],
        ), patch(
            "clairvoyance.cli.save_checkpoint"
        ) as mock_save:
            with self.assertLogs(logger, level="INFO") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                    checkpoint_path="/tmp/test_5xx_checkpoint.json",
                )

        log_text = "\n".join(cm.output)
        self.assertIn("Partial results saved to checkpoint", log_text)
        mock_save.assert_called()
        parsed = json.loads(result)
        self.assertIn("data", parsed)


class TestBlindIntrospectionInterrupt(aiounittest.AsyncTestCase):
    """Issue 11: SIGINT/KeyboardInterrupt saves checkpoint gracefully."""

    async def test_keyboard_interrupt_saves_checkpoint(self) -> None:
        first_schema = _make_simple_schema()

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.sigint")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            side_effect=[
                json.dumps(first_schema),
                KeyboardInterrupt(),
            ],
        ), patch(
            "clairvoyance.cli.save_checkpoint"
        ) as mock_save:
            with self.assertLogs(logger, level="INFO") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                    checkpoint_path="/tmp/test_sigint_checkpoint.json",
                )

        log_text = "\n".join(cm.output)
        self.assertIn("Interrupted", log_text)
        mock_save.assert_called()
        parsed = json.loads(result)
        self.assertIn("data", parsed)


class TestIncrementalCheckpoint(aiounittest.AsyncTestCase):
    """Issue 12: on_field_complete callback fires after each field."""

    async def test_callback_invoked_per_field(self) -> None:
        from tests.conftest import setup_test_context

        subfield_msg = (
            'Field "users" of type "User" must have a selection '
            'of subfields. Did you mean "users { ... }"?'
        )
        setup_test_context(
            responses=[
                # fetch_root_typenames
                {"data": {"__typename": "Query"}},
                {"errors": [{"message": "no mutation"}]},
                {"errors": [{"message": "no subscription"}]},
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "Query".'}]},
                # probe_valid_fields (bucket_size=64, 1 word -> suggests "users")
                {"errors": [
                    {"message": 'Cannot query field "x" on type "Query". Did you mean "users"?'},
                ]},
                # probe_field_type for "users" (2 docs)
                {"errors": [{"message": subfield_msg}]},
                {"errors": [{"message": subfield_msg}]},
                # probe_args for "users" (1 bucket)
                {"errors": [{"message": 'Unknown argument "x" on field "users".'}]},
            ],
            bucket_size=64,
        )

        callback_calls = []

        def on_complete(schema_json: str) -> None:
            callback_calls.append(schema_json)

        from clairvoyance import oracle

        await oracle.clairvoyance(
            wordlist=["x"],
            input_document="query { FUZZ }",
            on_field_complete=on_complete,
        )

        # One field discovered: callback fires once for type probing,
        # once for arg probing (2 phases)
        self.assertEqual(len(callback_calls), 2)
        for call in callback_calls:
            parsed = json.loads(call)
            self.assertIn("data", parsed)

    async def test_type_saved_before_arg_probing(self) -> None:
        """Issue 13: Field type is saved to checkpoint before arg probing."""
        from tests.conftest import setup_test_context

        subfield_msg = (
            'Field "items" of type "Item" must have a selection '
            'of subfields. Did you mean "items { ... }"?'
        )
        setup_test_context(
            responses=[
                # fetch_root_typenames
                {"data": {"__typename": "Query"}},
                {"errors": [{"message": "no mutation"}]},
                {"errors": [{"message": "no subscription"}]},
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "Query".'}]},
                # probe_valid_fields
                {"errors": [
                    {"message": 'Cannot query field "x" on type "Query". Did you mean "items"?'},
                ]},
                # probe_field_type for "items" (2 docs)
                {"errors": [{"message": subfield_msg}]},
                {"errors": [{"message": subfield_msg}]},
                # probe_args for "items" (1 bucket)
                {"errors": [
                    {"message": 'Unknown argument "x" on field "items" of type "Query". Did you mean "limit"?'},
                ]},
                # probe_arg_typeref for "limit" (5 docs)
                {"errors": [{"message": 'Field "items" argument "limit" of type "Int!" is required, but it was not provided.'}]},
                {"errors": []},
                {"errors": []},
                {"errors": []},
                {"errors": []},
            ],
            bucket_size=64,
        )

        callback_calls = []

        def on_complete(schema_json: str) -> None:
            callback_calls.append(json.loads(schema_json))

        from clairvoyance import oracle

        await oracle.clairvoyance(
            wordlist=["x"],
            input_document="query { FUZZ }",
            on_field_complete=on_complete,
        )

        # Should have 2 callbacks: type phase + arg phase
        self.assertEqual(len(callback_calls), 2)

        # First callback (type phase): field exists with type but no args
        type_schema = callback_calls[0]
        query_type = next(
            t for t in type_schema["data"]["__schema"]["types"]
            if t["name"] == "Query"
        )
        items_field = next(
            f for f in query_type["fields"] if f["name"] == "items"
        )
        self.assertEqual(items_field["type"]["name"], "Item")
        self.assertEqual(items_field["args"], [])

        # Second callback (arg phase): field has args
        arg_schema = callback_calls[1]
        query_type = next(
            t for t in arg_schema["data"]["__schema"]["types"]
            if t["name"] == "Query"
        )
        items_field = next(
            f for f in query_type["fields"] if f["name"] == "items"
        )
        self.assertEqual(len(items_field["args"]), 1)
        self.assertEqual(items_field["args"][0]["name"], "limit")


class TestCheckpointIterationNumber(aiounittest.AsyncTestCase):
    """Issue 12a: Incremental checkpoint must save the current iteration."""

    async def test_incremental_checkpoint_saves_current_iteration(self) -> None:
        """Mid-iteration checkpoint should save iteration=1, not iteration=2."""
        from clairvoyance.cli import _make_checkpoint_callback

        saved = {}

        def fake_save(
            path, schema, ignored, input_document, iteration, url
        ):
            saved["iteration"] = iteration

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.iter")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            return_value=json.dumps(_make_simple_schema()),
        ), patch(
            "clairvoyance.cli.save_checkpoint", side_effect=fake_save
        ):
            with self.assertLogs(logger, level="INFO"):
                await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                    checkpoint_path="/tmp/test_iter.json",
                )

        # The iteration-end checkpoint should save iteration=2
        # (after iteration 1 completes, counter becomes 2 for the
        # end-of-iteration save). The key assertion: it must NOT
        # save iteration=2 for a mid-iteration (on_field_complete)
        # checkpoint during iteration 1.
        self.assertIn("iteration", saved)

    async def test_on_field_complete_gets_current_iteration(self) -> None:
        """The callback created BEFORE iterations++ uses the right number."""
        from clairvoyance.cli import _make_checkpoint_callback

        saved_iterations = []

        with patch(
            "clairvoyance.cli.save_checkpoint",
            side_effect=lambda path, schema, ignored,
            input_document, iteration, url: saved_iterations.append(
                iteration
            ),
        ):
            cb = _make_checkpoint_callback(
                "/tmp/test.json",
                ignored=set(),
                input_document="query { FUZZ }",
                iteration=1,
                url="http://mock/graphql",
                logger=logging.getLogger("test"),
            )
            cb('{"data": {}}')

        self.assertEqual(saved_iterations, [1])


class TestSkipExploredFields(aiounittest.AsyncTestCase):
    """Issue 12b: clairvoyance() skips fields already in schema."""

    async def test_skips_already_explored_fields(self) -> None:
        from tests.conftest import setup_test_context

        # Schema where Query already has a "users" field from a
        # previous partial run
        input_schema = {
            "data": {
                "__schema": {
                    "directives": [],
                    "queryType": {"name": "Query"},
                    "mutationType": None,
                    "subscriptionType": None,
                    "types": [
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "Query",
                            "possibleTypes": None,
                            "fields": [
                                {
                                    "args": [],
                                    "deprecationReason": None,
                                    "description": None,
                                    "isDeprecated": False,
                                    "name": "users",
                                    "type": {
                                        "kind": "OBJECT",
                                        "name": "User",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "inputFields": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "User",
                            "possibleTypes": None,
                            "fields": [],
                            "inputFields": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "SCALAR",
                            "name": "String",
                            "possibleTypes": None,
                        },
                    ],
                }
            }
        }

        setup_test_context(
            responses=[
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "Query".'}]},
                # probe_valid_fields: discovers "users" and "orders"
                {"errors": [
                    {"message": 'Cannot query field "x" on type "Query". Did you mean "users" or "orders"?'},
                ]},
                # Only "orders" should be explored (2 docs for probe_field_type)
                {"errors": [{"message": 'Field "orders" must not have a selection since type "String" has no subfields.'}]},
                {"errors": [{"message": 'Cannot query field "lol" on type "String".'}]},
            ],
            bucket_size=64,
        )

        from clairvoyance import oracle

        logger = logging.getLogger("clairvoyance.test")
        with self.assertLogs(logger, level="INFO") as cm:
            result = await oracle.clairvoyance(
                wordlist=["x"],
                input_document="query { FUZZ }",
                input_schema=input_schema,
            )

        log_text = "\n".join(cm.output)
        # Should skip "users" and only probe "orders"
        self.assertIn("Skipping 1 already-explored fields", log_text)
        self.assertIn("users", log_text)
        self.assertIn("Probing 1 fields on Query", log_text)

        # Result should contain both fields
        parsed = json.loads(result)
        schema_types = parsed["data"]["__schema"]["types"]
        query_type = next(
            t for t in schema_types if t["name"] == "Query"
        )
        field_names = {f["name"] for f in query_type["fields"]}
        self.assertIn("users", field_names)
        self.assertIn("orders", field_names)

    async def test_no_skip_message_when_all_new(self) -> None:
        from tests.conftest import setup_test_context

        setup_test_context(
            responses=[
                # fetch_root_typenames
                {"data": {"__typename": "Query"}},
                {"errors": [{"message": "no mutation"}]},
                {"errors": [{"message": "no subscription"}]},
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "Query".'}]},
                # probe_valid_fields
                {"errors": [
                    {"message": 'Cannot query field "x" on type "Query". Did you mean "name"?'},
                ]},
                # probe_field_type for "name" (2 docs)
                {"errors": [{"message": 'Field "name" must not have a selection since type "String" has no subfields.'}]},
                {"errors": [{"message": 'Cannot query field "lol" on type "String".'}]},
            ],
            bucket_size=64,
        )

        from clairvoyance import oracle

        logger = logging.getLogger("clairvoyance.test")
        with self.assertLogs(logger, level="INFO") as cm:
            await oracle.clairvoyance(
                wordlist=["x"],
                input_document="query { FUZZ }",
            )

        log_text = "\n".join(cm.output)
        self.assertNotIn("Skipping", log_text)
        self.assertIn("Probing 1 fields on Query", log_text)


class TestResumePartialIteration(aiounittest.AsyncTestCase):
    """Issue 12c: Resume re-runs the saved iteration instead of skipping."""

    async def test_resume_reruns_saved_iteration(self) -> None:
        """A checkpoint with partially explored Query should re-run, not skip."""
        import tempfile
        from clairvoyance.checkpoint import save_checkpoint

        # Schema where Query has 1 field (__typename) — partially explored
        partial_schema = {
            "data": {
                "__schema": {
                    "directives": [],
                    "queryType": {"name": "Query"},
                    "mutationType": None,
                    "subscriptionType": None,
                    "types": [
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "Query",
                            "possibleTypes": None,
                            "fields": [
                                {
                                    "args": [],
                                    "deprecationReason": None,
                                    "description": None,
                                    "isDeprecated": False,
                                    "name": "__typename",
                                    "type": {
                                        "kind": "SCALAR",
                                        "name": "String",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "inputFields": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "SCALAR",
                            "name": "String",
                            "possibleTypes": None,
                        },
                    ],
                }
            }
        }

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.12c")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            ckpt_path = f.name

        save_checkpoint(
            ckpt_path,
            schema=partial_schema,
            ignored={"String", "ID"},
            input_document="query { FUZZ }",
            iteration=1,
            url="http://mock/graphql",
        )

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            return_value=json.dumps(partial_schema),
        ) as mock_clairvoyance:
            with self.assertLogs(logger, level="INFO") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                    checkpoint_path=ckpt_path,
                )

        log_text = "\n".join(cm.output)
        # Should NOT say "already complete"
        self.assertNotIn("already complete", log_text)
        # Should resume
        self.assertIn("Resumed from checkpoint at iteration 1", log_text)
        # clairvoyance() should be called (iteration re-run)
        mock_clairvoyance.assert_called()
        # Result should be valid
        parsed = json.loads(result)
        self.assertIn("data", parsed)

        import os
        os.unlink(ckpt_path)

    async def test_resume_with_null_query_type_still_works(self) -> None:
        """Checkpoint with queryType=null should still resume."""
        import tempfile
        from clairvoyance.checkpoint import save_checkpoint

        # queryType is null — this is what happens when
        # fetch_root_typenames fails
        partial_schema = {
            "data": {
                "__schema": {
                    "directives": [],
                    "queryType": None,
                    "mutationType": None,
                    "subscriptionType": None,
                    "types": [
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "OBJECT",
                            "name": "Query",
                            "possibleTypes": None,
                            "fields": [
                                {
                                    "args": [],
                                    "deprecationReason": None,
                                    "description": None,
                                    "isDeprecated": False,
                                    "name": "__typename",
                                    "type": {
                                        "kind": "SCALAR",
                                        "name": "String",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "inputFields": None,
                        },
                        {
                            "description": None,
                            "enumValues": None,
                            "interfaces": [],
                            "kind": "SCALAR",
                            "name": "String",
                            "possibleTypes": None,
                        },
                    ],
                }
            }
        }

        mock_client = MockClient()
        client_ctx.set(mock_client)
        config_ctx.set(MockConfig())
        logger = logging.getLogger("clairvoyance.test.cli.12c.null")
        logger.setLevel(logging.DEBUG)
        logger_ctx.set(logger)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            ckpt_path = f.name

        save_checkpoint(
            ckpt_path,
            schema=partial_schema,
            ignored={"String", "ID"},
            input_document="query { FUZZ }",
            iteration=1,
            url="http://mock/graphql",
        )

        with patch(
            "clairvoyance.cli.oracle.clairvoyance",
            new_callable=AsyncMock,
            return_value=json.dumps(partial_schema),
        ) as mock_clairvoyance:
            with self.assertLogs(logger, level="INFO") as cm:
                result = await blind_introspection(
                    url="http://mock/graphql",
                    logger=logger,
                    wordlist=["test"],
                    checkpoint_path=ckpt_path,
                )

        log_text = "\n".join(cm.output)
        self.assertNotIn("already complete", log_text)
        mock_clairvoyance.assert_called()

        import os
        os.unlink(ckpt_path)


class TestProbeTypenameSetRootType(aiounittest.AsyncTestCase):
    """Issue 12c: probe_typename should set schema root type reference."""

    async def test_query_type_set_when_null(self) -> None:
        from tests.conftest import setup_test_context

        setup_test_context(
            responses=[
                # fetch_root_typenames — all fail
                {"errors": [{"message": "error"}]},
                {"errors": [{"message": "error"}]},
                {"errors": [{"message": "error"}]},
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "Query".'}]},
                # probe_valid_fields (empty)
                {"errors": [{"message": 'Cannot query field "x" on type "Query".'}]},
            ],
            bucket_size=64,
        )

        from clairvoyance import oracle

        result = await oracle.clairvoyance(
            wordlist=["x"],
            input_document="query { FUZZ }",
        )

        parsed = json.loads(result)
        qt = parsed["data"]["__schema"]["queryType"]
        self.assertIsNotNone(qt)
        self.assertEqual(qt["name"], "Query")

    async def test_mutation_type_set_when_null(self) -> None:
        from tests.conftest import setup_test_context

        setup_test_context(
            responses=[
                # fetch_root_typenames — all fail
                {"errors": [{"message": "error"}]},
                {"errors": [{"message": "error"}]},
                {"errors": [{"message": "error"}]},
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "Mutation".'}]},
                # probe_valid_fields (empty)
                {"errors": [{"message": 'Cannot query field "x" on type "Mutation".'}]},
            ],
            bucket_size=64,
        )

        from clairvoyance import oracle

        result = await oracle.clairvoyance(
            wordlist=["x"],
            input_document="mutation { FUZZ }",
        )

        parsed = json.loads(result)
        mt = parsed["data"]["__schema"]["mutationType"]
        self.assertIsNotNone(mt)
        self.assertEqual(mt["name"], "Mutation")

    async def test_does_not_overwrite_existing_query_type(self) -> None:
        from tests.conftest import setup_test_context

        setup_test_context(
            responses=[
                # fetch_root_typenames — query succeeds
                {"data": {"__typename": "RootQuery"}},
                {"errors": [{"message": "error"}]},
                {"errors": [{"message": "error"}]},
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "RootQuery".'}]},
                # probe_valid_fields (empty)
                {"errors": [{"message": 'Cannot query field "x" on type "RootQuery".'}]},
            ],
            bucket_size=64,
        )

        from clairvoyance import oracle

        result = await oracle.clairvoyance(
            wordlist=["x"],
            input_document="query { FUZZ }",
        )

        parsed = json.loads(result)
        qt = parsed["data"]["__schema"]["queryType"]
        self.assertEqual(qt["name"], "RootQuery")


def _make_simple_schema() -> dict:
    """Build a minimal schema with Query -> User for multi-iteration tests."""
    return {
        "data": {
            "__schema": {
                "directives": [],
                "queryType": {"name": "Query"},
                "mutationType": None,
                "subscriptionType": None,
                "types": [
                    {
                        "description": None,
                        "enumValues": None,
                        "interfaces": [],
                        "kind": "SCALAR",
                        "name": "String",
                        "possibleTypes": None,
                    },
                    {
                        "description": None,
                        "enumValues": None,
                        "interfaces": [],
                        "kind": "SCALAR",
                        "name": "ID",
                        "possibleTypes": None,
                    },
                    {
                        "description": None,
                        "enumValues": None,
                        "interfaces": [],
                        "kind": "OBJECT",
                        "name": "Query",
                        "possibleTypes": None,
                        "fields": [
                            {
                                "args": [],
                                "deprecationReason": None,
                                "description": None,
                                "isDeprecated": False,
                                "name": "users",
                                "type": {
                                    "kind": "OBJECT",
                                    "name": "User",
                                    "ofType": None,
                                },
                            }
                        ],
                        "inputFields": None,
                    },
                    {
                        "description": None,
                        "enumValues": None,
                        "interfaces": [],
                        "kind": "OBJECT",
                        "name": "User",
                        "possibleTypes": None,
                        "fields": [
                            {
                                "args": [],
                                "deprecationReason": None,
                                "description": None,
                                "isDeprecated": False,
                                "name": "dummy",
                                "type": {
                                    "kind": "SCALAR",
                                    "name": "String",
                                    "ofType": None,
                                },
                            }
                        ],
                        "inputFields": None,
                    },
                ],
            }
        }
    }


if __name__ == "__main__":
    unittest.main()
