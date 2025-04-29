import abc

from lanraragi.clients.api_context import ApiContextManager

class ApiClient(abc.ABC):
    """
    An abstract base class that represents an organized collection of APIs on a client. (Search, Archive, Database, etc.)
    API groups are not clients, they will call the client's methods.
    """

    def __init__(self, context: ApiContextManager):
        self.api_context = context
