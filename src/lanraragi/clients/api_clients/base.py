import abc
from typing import Dict

from lanraragi.clients.api_context import ApiContextManager

class _ApiClient(abc.ABC):
    """
    A private abstract base class that represents an organized collection of APIs on a client. (Search, Archive, Database, etc.)
    API groups are not clients, they will call the client's methods.
    """

    @property
    def headers(self) -> Dict[str, str]:
        """
        LRR request headers. Is either an empty dict or contains authentication.
        """
        return self.api_context.headers

    def __init__(self, context: ApiContextManager):
        self.api_context = context

__all__ = [
    "_ApiClient"
]
