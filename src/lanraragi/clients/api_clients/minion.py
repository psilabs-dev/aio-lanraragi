import http
import json

from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.res_processors.minion import (
    _process_get_minion_job_detail_response,
    _process_queued_minion_job_response,
)
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.models.minion import (
    GetMinionJobDetailRequest,
    GetMinionJobDetailResponse,
    GetMinionJobStatusRequest,
    GetMinionJobStatusResponse,
    QueuedMinionJobResponse,
    QueueMinionJobRequest,
)


class _MinionApiClient(_ApiClient):


    async def get_minion_job_status(self, request: GetMinionJobStatusRequest) -> _LRRClientResponse[GetMinionJobStatusResponse]:
        """
        GET /api/minion/:jobid
        """
        url = self.api_context.build_url(f"/api/minion/{request.job_id}")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            state = response_j.get("state")
            task = response_j.get("task")
            error = response_j.get("error")
            notes = response_j.get("notes")
            return (GetMinionJobStatusResponse(state=state, task=task, error=error, notes=notes), None)
        return (None, _build_err_response(content, status))

    async def get_minion_job_details(self, request: GetMinionJobDetailRequest) -> _LRRClientResponse[GetMinionJobDetailResponse]:
        """
        GET /api/minion/:jobid/detail
        """
        url = self.api_context.build_url(f"/api/minion/{request.job_id}/detail")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (_process_get_minion_job_detail_response(content), None)
        return (None, _build_err_response(content, status))

    async def queue_minion_job(self, request: QueueMinionJobRequest) -> _LRRClientResponse[QueuedMinionJobResponse]:
        url = self.api_context.build_url(f"/api/minion/{request.job_name}/queue")
        params = {"args": json.dumps(request.args)}
        if request.priority is not None:
            params["priority"] = request.priority
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, params=params)
        return _process_queued_minion_job_response(content, status)


__all__ = [
    "_MinionApiClient"
]
