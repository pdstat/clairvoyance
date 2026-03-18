"""Mock infrastructure for oracle and client unit tests."""

import asyncio
import logging
from typing import Dict, List, Optional

from clairvoyance.entities.context import client_ctx, config_ctx, logger_ctx
from clairvoyance.entities.interfaces import IClient, IConfig


class MockConfig(IConfig):
    def __init__(self, bucket_size: int = 64) -> None:
        self._bucket_size = bucket_size


class MockClient(IClient):
    """A mock client that returns pre-configured responses in order."""

    def __init__(self, responses: Optional[List[Dict]] = None) -> None:
        self._responses = responses or []
        self._call_index = 0
        self._url = "http://mock/graphql"
        self._headers: Dict[str, str] = {}
        self._max_retries = 3
        self._session = None
        self._semaphore = asyncio.Semaphore(50)

    async def post(
        self,
        document: Optional[str] = None,
        retries: int = 0,
    ) -> Dict:
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        return {"errors": []}

    async def close(self) -> None:
        pass


def setup_test_context(
    responses: Optional[List[Dict]] = None,
    bucket_size: int = 64,
) -> MockClient:
    """Wire MockClient, MockConfig, and a logger into ContextVars."""
    mock_config = MockConfig(bucket_size=bucket_size)
    config_ctx.set(mock_config)

    mock_client = MockClient(responses=responses)
    client_ctx.set(mock_client)

    logger = logging.getLogger("clairvoyance.test")
    logger.setLevel(logging.DEBUG)
    logger_ctx.set(logger)

    return mock_client
