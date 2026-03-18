"""Tests for client.py error handling and retry behavior."""

import asyncio
import json
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import aiounittest

from clairvoyance.client import Client
from clairvoyance.entities.context import logger_ctx

logger_ctx.set(logging.getLogger("clairvoyance.test"))


class TestMaxRetries(aiounittest.AsyncTestCase):
    """Bug 7: post() returned {} on max retries, missing 'errors' key."""

    async def test_max_retries_returns_errors_key(self) -> None:
        c = Client("http://localhost:1/nonexistent", max_retries=0)
        result = await c.post("query { test }")
        self.assertIn("errors", result)
        self.assertEqual(result["errors"], [])
        await c.close()

    async def test_max_retries_with_retries_param(self) -> None:
        c = Client("http://localhost:1/nonexistent", max_retries=3)
        result = await c.post("query { test }", retries=3)
        self.assertIn("errors", result)
        await c.close()


class TestClientTimeout(unittest.TestCase):
    """Bug 9: No timeout on aiohttp.post()."""

    def test_client_has_default_timeout(self) -> None:
        c = Client("http://localhost:1/test")
        self.assertIsInstance(c._timeout, aiohttp.ClientTimeout)
        self.assertEqual(c._timeout.total, 60)


class TestClientSessionLock(unittest.TestCase):
    """Bug 10: Race condition in lazy session creation."""

    def test_client_has_session_lock(self) -> None:
        c = Client("http://localhost:1/test")
        self.assertIsInstance(c._session_lock, asyncio.Lock)


def _make_mock_response(status=200, json_data=None, raise_json_error=False):
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    if raise_json_error:
        resp.json = AsyncMock(
            side_effect=json.decoder.JSONDecodeError("bad", "", 0)
        )
    else:
        resp.json = AsyncMock(return_value=json_data or {"errors": []})
    return resp


class TestRetryOn500(aiounittest.AsyncTestCase):
    async def test_retries_on_500_then_succeeds(self) -> None:
        c = Client("http://test/graphql", max_retries=3)

        resp_500 = _make_mock_response(status=500)
        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"__typename": "Query"}},
        )

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=[resp_500, resp_ok])
        c._session = mock_session

        result = await c.post("query { __typename }")
        self.assertEqual(result, {"data": {"__typename": "Query"}})
        self.assertEqual(mock_session.post.call_count, 2)

    async def test_500_exhausts_retries(self) -> None:
        c = Client("http://test/graphql", max_retries=2)

        resp_500 = _make_mock_response(status=500)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_500)
        c._session = mock_session

        result = await c.post("query { __typename }")
        self.assertIn("errors", result)
        self.assertEqual(result["errors"], [])


class TestRetryOnJsonDecodeError(aiounittest.AsyncTestCase):
    async def test_retries_on_json_decode_error(self) -> None:
        c = Client("http://test/graphql", max_retries=3)

        resp_bad = _make_mock_response(status=200, raise_json_error=True)
        resp_ok = _make_mock_response(
            status=200,
            json_data={"errors": [{"message": "some error"}]},
        )

        mock_session = MagicMock()
        mock_session.post = AsyncMock(side_effect=[resp_bad, resp_ok])
        c._session = mock_session

        result = await c.post("query { test }")
        self.assertEqual(
            result, {"errors": [{"message": "some error"}]}
        )

    async def test_json_decode_error_exhausts_retries(self) -> None:
        c = Client("http://test/graphql", max_retries=1)

        resp_bad = _make_mock_response(status=200, raise_json_error=True)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_bad)
        c._session = mock_session

        result = await c.post("query { test }")
        self.assertIn("errors", result)
        self.assertEqual(result["errors"], [])


class TestRetryOnConnectionError(aiounittest.AsyncTestCase):
    async def test_retries_on_connection_error(self) -> None:
        c = Client("http://test/graphql", max_retries=3)

        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"ok": True}},
        )

        mock_session = MagicMock()
        mock_session.post = AsyncMock(
            side_effect=[
                aiohttp.ClientConnectionError("refused"),
                resp_ok,
            ]
        )
        c._session = mock_session

        result = await c.post("query { test }")
        self.assertEqual(result, {"data": {"ok": True}})

    async def test_retries_on_timeout_error(self) -> None:
        c = Client("http://test/graphql", max_retries=3)

        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"ok": True}},
        )

        mock_session = MagicMock()
        mock_session.post = AsyncMock(
            side_effect=[asyncio.TimeoutError(), resp_ok]
        )
        c._session = mock_session

        result = await c.post("query { test }")
        self.assertEqual(result, {"data": {"ok": True}})

    async def test_retries_on_payload_error(self) -> None:
        c = Client("http://test/graphql", max_retries=3)

        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"ok": True}},
        )

        mock_session = MagicMock()
        mock_session.post = AsyncMock(
            side_effect=[aiohttp.ClientPayloadError("truncated"), resp_ok]
        )
        c._session = mock_session

        result = await c.post("query { test }")
        self.assertEqual(result, {"data": {"ok": True}})

    async def test_connection_error_exhausts_retries(self) -> None:
        c = Client("http://test/graphql", max_retries=2)

        mock_session = MagicMock()
        mock_session.post = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("refused")
        )
        c._session = mock_session

        result = await c.post("query { test }")
        self.assertIn("errors", result)
        self.assertEqual(result["errors"], [])


class TestBackoff(aiounittest.AsyncTestCase):
    async def test_backoff_delay_applied(self) -> None:
        c = Client("http://test/graphql", max_retries=2, backoff=2)

        resp_bad = _make_mock_response(status=200, raise_json_error=True)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_bad)
        c._session = mock_session

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await c.post("query { test }")
            # retry 0: delay = 0.5 * 2**0 = 0.5
            # retry 1: delay = 0.5 * 2**1 = 1.0
            self.assertEqual(mock_sleep.call_count, 2)
            mock_sleep.assert_any_call(0.5)
            mock_sleep.assert_any_call(1.0)

    async def test_no_backoff_when_not_configured(self) -> None:
        c = Client("http://test/graphql", max_retries=1)

        resp_bad = _make_mock_response(status=200, raise_json_error=True)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_bad)
        c._session = mock_session

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await c.post("query { test }")
            mock_sleep.assert_not_called()


class TestPostReturnShape(aiounittest.AsyncTestCase):
    """Verify post() always returns a dict with 'errors' or 'data'."""

    async def test_successful_response_passes_through(self) -> None:
        c = Client("http://test/graphql")
        expected = {
            "data": {"users": [{"name": "Alice"}]},
            "errors": [{"message": "partial error"}],
        }
        resp = _make_mock_response(status=200, json_data=expected)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp)
        c._session = mock_session

        result = await c.post("query { users { name } }")
        self.assertEqual(result, expected)

    async def test_4xx_response_passes_through(self) -> None:
        """4xx errors are not retried — the JSON is returned as-is."""
        c = Client("http://test/graphql")
        expected = {"errors": [{"message": "Unauthorized"}]}
        resp = _make_mock_response(status=401, json_data=expected)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp)
        c._session = mock_session

        result = await c.post("query { test }")
        self.assertEqual(result, expected)

    async def test_null_document(self) -> None:
        """Passing None as document sends null JSON body."""
        c = Client("http://test/graphql")
        expected = {"errors": [{"message": "Must provide query string"}]}
        resp = _make_mock_response(status=400, json_data=expected)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp)
        c._session = mock_session

        result = await c.post(None)
        self.assertEqual(result, expected)
        call_kwargs = mock_session.post.call_args
        self.assertIsNone(call_kwargs.kwargs.get("json"))


if __name__ == "__main__":
    unittest.main()
