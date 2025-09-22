from typing import override
import aiohttp
from lanraragi.clients.client import LRRClient

class IntegrationTestLRRClient(LRRClient):
    """
    A LRR client optimized for integration tests, namely tighter controls over network connection limits.
    """

    @override
    async def _get_session(self) -> aiohttp.ClientSession:
        if not self.session:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    limit=8, limit_per_host=8, keepalive_timeout=30
                )
            )
            self._created_session = True
        return self.session

    @override
    async def close(self):
        if (session := self.session) and self._created_session:
            if (connector := session.connector):
                await connector.close()
            await session.close()
            self.session = None
            self._created_session = False
