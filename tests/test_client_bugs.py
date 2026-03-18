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


class TestDefaultUserAgent(unittest.TestCase):
    def test_default_user_agent_set(self) -> None:
        c = Client("http://test/graphql")
        self.assertIn("User-Agent", c._headers)
        self.assertIn("Mozilla", c._headers["User-Agent"])

    def test_custom_user_agent_preserved(self) -> None:
        c = Client("http://test/graphql", headers={"User-Agent": "custom/1.0"})
        self.assertEqual(c._headers["User-Agent"], "custom/1.0")

    def test_case_insensitive_check(self) -> None:
        c = Client("http://test/graphql", headers={"user-agent": "custom/1.0"})
        self.assertEqual(c._headers["user-agent"], "custom/1.0")
        self.assertNotIn("User-Agent", c._headers)


class TestConsecutiveAuthErrors(aiounittest.AsyncTestCase):
    """Issue 1: Abort after N consecutive 401/403 responses."""

    async def test_raises_auth_error_after_threshold(self) -> None:
        from clairvoyance.entities.errors import AuthError

        # max_retries=1 means each post() tries once then gives up
        c = Client(
            "http://test/graphql",
            max_retries=1,
            max_consecutive_auth_errors=3,
        )
        # 403 with valid JSON — no JSON decode error, no retry loop
        resp_403 = _make_mock_response(
            status=403,
            json_data={"errors": [{"message": "Forbidden"}]},
        )
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_403)
        c._session = mock_session

        # First two 403s increment but don't raise
        await c.post("query { a }")
        self.assertEqual(c._consecutive_auth_errors, 1)
        await c.post("query { b }")
        self.assertEqual(c._consecutive_auth_errors, 2)
        # Third 403 hits the threshold
        with self.assertRaises(AuthError) as ctx:
            await c.post("query { c }")
        self.assertIn("3 consecutive HTTP 403", str(ctx.exception))

    async def test_successful_response_resets_counter(self) -> None:
        c = Client(
            "http://test/graphql",
            max_retries=1,
            max_consecutive_auth_errors=5,
        )
        resp_403 = _make_mock_response(
            status=403,
            json_data={"errors": [{"message": "Forbidden"}]},
        )
        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"ok": True}},
        )
        mock_session = MagicMock()
        mock_session.post = AsyncMock(
            side_effect=[resp_403, resp_403, resp_ok, resp_403, resp_403]
        )
        c._session = mock_session

        # Two 403s, then a success resets the counter
        await c.post("query { a }")
        await c.post("query { b }")
        self.assertEqual(c._consecutive_auth_errors, 2)
        await c.post("query { c }")  # 200 OK — resets
        self.assertEqual(c._consecutive_auth_errors, 0)
        # Two more 403s — still under threshold
        await c.post("query { d }")
        await c.post("query { e }")
        self.assertEqual(c._consecutive_auth_errors, 2)

    async def test_401_also_tracked(self) -> None:
        from clairvoyance.entities.errors import AuthError

        c = Client(
            "http://test/graphql",
            max_retries=0,
            max_consecutive_auth_errors=2,
        )
        resp_401 = _make_mock_response(
            status=401,
            json_data={"errors": [{"message": "Unauthorized"}]},
        )
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_401)
        c._session = mock_session

        await c.post("query { a }")
        with self.assertRaises(AuthError) as ctx:
            await c.post("query { b }")
        self.assertIn("2 consecutive HTTP 401", str(ctx.exception))

    async def test_default_threshold_is_ten(self) -> None:
        c = Client("http://test/graphql")
        self.assertEqual(c._max_consecutive_auth_errors, 10)

    async def test_403_with_json_body_still_tracked(self) -> None:
        """A 403 with valid JSON (e.g. GraphQL error) still counts."""
        from clairvoyance.entities.errors import AuthError

        c = Client(
            "http://test/graphql",
            max_retries=0,
            max_consecutive_auth_errors=2,
        )
        resp_403_json = _make_mock_response(
            status=403,
            json_data={"errors": [{"message": "Forbidden"}]},
        )
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_403_json)
        c._session = mock_session

        await c.post("query { a }")
        with self.assertRaises(AuthError):
            await c.post("query { b }")


class TestRetryLogging(aiounittest.AsyncTestCase):
    """Retry attempts emit INFO logs with HTTP status code."""

    async def test_500_retry_logs_status(self) -> None:
        c = Client("http://test/graphql", max_retries=2, backoff=2)

        resp_500 = _make_mock_response(status=500)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_500)
        c._session = mock_session

        logger = logging.getLogger("clairvoyance.test")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with self.assertLogs(logger, level="INFO") as cm:
                await c.post("query { test }")

        log_text = "\n".join(cm.output)
        self.assertIn("Retry 1/2 after HTTP 500", log_text)

    async def test_json_decode_retry_logs_status(self) -> None:
        c = Client("http://test/graphql", max_retries=1)

        resp_bad = _make_mock_response(status=403, raise_json_error=True)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_bad)
        c._session = mock_session

        logger = logging.getLogger("clairvoyance.test")
        with self.assertLogs(logger, level="INFO") as cm:
            await c.post("query { test }")

        log_text = "\n".join(cm.output)
        self.assertIn("Retry 1/1 after HTTP 403", log_text)

    async def test_connection_error_retry_logs(self) -> None:
        c = Client("http://test/graphql", max_retries=1)

        mock_session = MagicMock()
        mock_session.post = AsyncMock(
            side_effect=aiohttp.ClientConnectionError("refused")
        )
        c._session = mock_session

        logger = logging.getLogger("clairvoyance.test")
        with self.assertLogs(logger, level="INFO") as cm:
            await c.post("query { test }")

        log_text = "\n".join(cm.output)
        self.assertIn("Retry 1/1", log_text)
        # No HTTP status for connection errors
        self.assertNotIn("after HTTP", log_text)

    async def test_no_backoff_still_logs_retry(self) -> None:
        c = Client("http://test/graphql", max_retries=1)

        resp_bad = _make_mock_response(status=200, raise_json_error=True)
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_bad)
        c._session = mock_session

        logger = logging.getLogger("clairvoyance.test")
        with self.assertLogs(logger, level="INFO") as cm:
            await c.post("query { test }")

        log_text = "\n".join(cm.output)
        self.assertIn("Retry 1/1 after HTTP 200", log_text)
        self.assertIn("backoff 0.0s", log_text)


class TestRateLimit(aiounittest.AsyncTestCase):
    async def test_rate_limit_paces_requests(self) -> None:
        # 10 req/s = 0.1s between requests
        c = Client("http://test/graphql", max_retries=1, rate_limit=10)
        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"ok": True}},
        )
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_ok)
        c._session = mock_session

        self.assertAlmostEqual(c._rate_limit_delay, 0.1, places=5)

        start = asyncio.get_event_loop().time()
        await c.post("query { a }")
        await c.post("query { b }")
        await c.post("query { c }")
        elapsed = asyncio.get_event_loop().time() - start

        # 3 requests at 10/s should take at least 0.2s (2 gaps)
        self.assertGreaterEqual(elapsed, 0.15)

    def test_no_rate_limit_by_default(self) -> None:
        c = Client("http://test/graphql")
        self.assertEqual(c._rate_limit_delay, 0)

    def test_rate_limit_delay_calculation(self) -> None:
        c = Client("http://test/graphql", rate_limit=5)
        self.assertAlmostEqual(c._rate_limit_delay, 0.2, places=5)


class TestCookieJar(unittest.TestCase):
    def test_cookies_enabled_by_default(self) -> None:
        c = Client("http://test/graphql")
        self.assertFalse(c._disable_cookies)

    def test_cookies_disabled_flag(self) -> None:
        c = Client("http://test/graphql", disable_cookies=True)
        self.assertTrue(c._disable_cookies)


class TestCookieJarSession(aiounittest.AsyncTestCase):
    async def test_session_uses_real_cookie_jar(self) -> None:
        c = Client("http://test/graphql", max_retries=1)
        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"ok": True}},
        )
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_ok)

        with patch("aiohttp.ClientSession") as mock_cls:
            mock_cls.return_value = mock_session
            c._session = None
            await c.post("query { test }")
            call_kwargs = mock_cls.call_args.kwargs
            self.assertIsInstance(
                call_kwargs["cookie_jar"], aiohttp.CookieJar
            )

    async def test_session_uses_dummy_jar_when_disabled(self) -> None:
        c = Client(
            "http://test/graphql", max_retries=1, disable_cookies=True
        )
        resp_ok = _make_mock_response(
            status=200,
            json_data={"data": {"ok": True}},
        )
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=resp_ok)

        with patch("aiohttp.ClientSession") as mock_cls:
            mock_cls.return_value = mock_session
            c._session = None
            await c.post("query { test }")
            call_kwargs = mock_cls.call_args.kwargs
            self.assertIsInstance(
                call_kwargs["cookie_jar"], aiohttp.DummyCookieJar
            )


if __name__ == "__main__":
    unittest.main()
