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


if __name__ == "__main__":
    unittest.main()
