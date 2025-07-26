import http
from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.models.metrics import GetMetricsResponse
from prometheus_client.parser import text_string_to_metric_families

class _MetricsApiClient(_ApiClient):
    
    async def get_metrics(self) -> _LRRClientResponse[GetMetricsResponse]:
        """
        GET /api/metrics
        """
        url = self.api_context.build_url("/api/metrics")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers)
        if status == 200:
            return (GetMetricsResponse(metrics=text_string_to_metric_families(content)), None)
        return (None, _build_err_response(content, status))

__all__ = [
    "_MetricsApiClient"
]
