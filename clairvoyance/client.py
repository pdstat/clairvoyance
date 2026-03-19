import asyncio
import json
import time
from typing import Dict, Optional

import aiohttp

from clairvoyance.entities.context import client_ctx, log
from clairvoyance.entities.errors import AuthError, ServerError
from clairvoyance.entities.interfaces import IClient


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class Client(IClient):  # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        url: str,
        max_retries: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        concurrent_requests: Optional[int] = None,
        proxy: Optional[str] = None,
        backoff: Optional[int] = None,
        disable_ssl_verify: Optional[bool] = None,
        max_consecutive_auth_errors: int = 10,
        max_consecutive_server_errors: int = 10,
        rate_limit: Optional[float] = None,
        disable_cookies: bool = False,
    ) -> None:
        self._url = url
        self._session = None

        self._headers = headers or {}
        if not any(k.lower() == "user-agent" for k in self._headers):
            self._headers["User-Agent"] = DEFAULT_USER_AGENT
        self._max_retries = max_retries or 3
        self._timeout = aiohttp.ClientTimeout(total=60)
        self._semaphore = asyncio.Semaphore(concurrent_requests or 50)
        self.proxy = proxy
        self.backoff = backoff
        self._backoff_semaphore = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        self.disable_ssl_verify = disable_ssl_verify or False
        self._consecutive_auth_errors = 0
        self._max_consecutive_auth_errors = max_consecutive_auth_errors
        self._consecutive_server_errors = 0
        self._max_consecutive_server_errors = max_consecutive_server_errors
        self._error_lock = asyncio.Lock()
        self._rate_limit_delay = 1.0 / rate_limit if rate_limit else 0
        self._rate_limit_lock = asyncio.Lock()
        self._last_request_time = 0.0
        self._disable_cookies = disable_cookies

        client_ctx.set(self)

    async def post(
        self,
        document: Optional[str],
        retries: int = 0,
    ) -> Dict:
        """Post a GraphQL document and return the JSON response.

        Retries are handled via a loop (not recursion) to avoid
        re-acquiring the semaphore on each retry attempt.
        """
        while retries < self._max_retries:
            result = await self._do_post(document, retries)
            if result is not None:
                return result
            retries += 1

        log().warning(
            f"Max retries ({self._max_retries}) exceeded for {self._url}"
        )
        return {"errors": []}

    async def _do_post(
        self,
        document: Optional[str],
        retries: int,
    ) -> Optional[Dict]:
        """Execute one POST attempt. Returns None to signal retry."""
        async with self._semaphore:
            await self._ensure_session()

            if self._rate_limit_delay:
                async with self._rate_limit_lock:
                    elapsed = time.monotonic() - self._last_request_time
                    wait = self._rate_limit_delay - elapsed
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._last_request_time = time.monotonic()

            gql_document = {"query": document} if document else None
            try:
                response = await self._session.post(
                    self._url,
                    json=gql_document,
                    proxy=self.proxy,
                    timeout=self._timeout,
                )

                if response.status in (401, 403):
                    await self._track_auth_error(response.status)

                if response.status >= 500:
                    await self._track_server_error(response.status)
                    await self._retry_backoff(
                        retries, response.status, document
                    )
                    return None

                try:
                    result = await response.json(content_type=None)
                    self._reset_error_counters(response.status)
                    return result
                except json.decoder.JSONDecodeError as e:
                    log().warning(
                        f"JSON decode error from {self._url} "
                        f"(status {response.status}): {e}"
                    )
                    await self._retry_backoff(
                        retries, response.status, document
                    )
                    return None

            except (
                aiohttp.ClientConnectionError,
                aiohttp.ClientPayloadError,
                asyncio.TimeoutError,
            ) as e:
                log().warning(
                    f"Connection error while POSTing to {self._url}: {e}"
                )
                await self._retry_backoff(retries, 0, document)
                return None

    async def _ensure_session(self) -> None:
        if not self._session:
            async with self._session_lock:
                if not self._session:
                    connector = aiohttp.TCPConnector(
                        ssl=not self.disable_ssl_verify
                    )
                    jar = (
                        aiohttp.DummyCookieJar()
                        if self._disable_cookies
                        else aiohttp.CookieJar()
                    )
                    self._session = aiohttp.ClientSession(
                        headers=self._headers,
                        connector=connector,
                        cookie_jar=jar,
                    )

    def _reset_error_counters(self, status: int) -> None:
        """Reset error counters on a successful (non-error) response."""
        if status not in (401, 403):
            self._consecutive_auth_errors = 0
        self._consecutive_server_errors = 0

    async def _track_auth_error(self, status_code: int) -> None:
        """Increment consecutive auth error counter; raise if threshold hit."""
        async with self._error_lock:
            self._consecutive_auth_errors += 1
            count = self._consecutive_auth_errors
        if count >= self._max_consecutive_auth_errors:
            raise AuthError(
                f"Received {count} consecutive HTTP {status_code} responses. "
                f"Token may have expired or endpoint is rejecting requests. "
                f"Partial results may be available via checkpoint."
            )

    async def _track_server_error(self, status_code: int) -> None:
        """Increment consecutive 5xx counter; raise if threshold hit."""
        async with self._error_lock:
            self._consecutive_server_errors += 1
            count = self._consecutive_server_errors
        log().warning(f"Received status code {status_code}")
        if count >= self._max_consecutive_server_errors:
            raise ServerError(
                f"Received {count} consecutive HTTP 5xx responses. "
                f"Server may be down or unresponsive. "
                f"Partial results may be available via checkpoint."
            )

    async def _retry_backoff(
        self,
        retries: int,
        status_code: int,
        document: Optional[str],
    ) -> None:
        """Log the retry attempt and sleep if backoff is configured."""
        status_part = f" after HTTP {status_code}" if status_code else ""
        delay = 0.5 * self.backoff**retries if self.backoff else 0
        log().info(
            f"Retry {retries + 1}/{self._max_retries}{status_part} "
            f"(backoff {delay:.1f}s)"
        )
        if self.backoff:
            async with self._backoff_semaphore:
                await asyncio.sleep(delay)

    async def close(self) -> None:
        if self._session:
            await self._session.close()
