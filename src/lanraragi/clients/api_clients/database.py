import http
import json

import aiohttp

from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.res_processors.database import (
    _process_get_database_backup_response,
    _process_get_database_stats_response,
)
from lanraragi.clients.res_processors.minion import _process_queued_minion_job_response
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.base import LanraragiResponse
from lanraragi.models.database import (
    CleanDatabaseResponse,
    DownloadDatabaseBackupRequest,
    DownloadDatabaseBackupResponse,
    GetDatabaseBackupResponse,
    GetDatabaseStatsRequest,
    GetDatabaseStatsResponse,
    QueueDatabaseRestoreRequest,
)
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.models.minion import QueuedMinionJobResponse


class _DatabaseApiClient(_ApiClient):

    async def get_database_stats(self, request: GetDatabaseStatsRequest) -> _LRRClientResponse[GetDatabaseStatsResponse]:
        """
        GET /api/database/stats
        """
        url = self.api_context.build_url("/api/database/stats")
        params = {}
        params["minweight"] = request.minweight
        if request.hide_excluded_namespaces is not None:
            params["hide_excluded_namespaces"] = str(request.hide_excluded_namespaces).lower()
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers, params=params)
        if status == 200:
            return (_process_get_database_stats_response(content), None)
        return (None, _build_err_response(content, status))

    async def clean_database(self) -> _LRRClientResponse[CleanDatabaseResponse]:
        """
        POST /api/database/clean
        """
        url = self.api_context.build_url("/api/database/clean")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            deleted = response_j.get("deleted")
            unlinked = response_j.get("unlinked")
            return (CleanDatabaseResponse(deleted=deleted, unlinked=unlinked), None)
        return (None, _build_err_response(content, status))

    async def drop_database(self) -> _LRRClientResponse[LanraragiResponse]:
        """
        POST /api/database/drop
        """
        url = self.api_context.build_url("/api/database/drop")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

    async def get_database_backup(self) -> _LRRClientResponse[GetDatabaseBackupResponse]:
        """
        GET /api/database/backup
        """
        url = self.api_context.build_url("/api/database/backup")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            return (_process_get_database_backup_response(content), None)
        return (None, _build_err_response(content, status))

    async def queue_database_backup(self) -> _LRRClientResponse[QueuedMinionJobResponse]:
        url = self.api_context.build_url("/api/database/backup")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers)
        return _process_queued_minion_job_response(content, status)

    async def download_database_backup(
            self, request: DownloadDatabaseBackupRequest
    ) -> _LRRClientResponse[DownloadDatabaseBackupResponse]:
        url = self.api_context.build_url(f"/api/database/backup/{request.job_id}")
        params = {"format": request.format} if request.format is not None else None
        status, content = await self.api_context.download_file(url, self.headers, params=params)
        if status == 200:
            return (DownloadDatabaseBackupResponse(data=content), None)
        return (None, _build_err_response(content, status))

    async def queue_database_restore(self, request: QueueDatabaseRestoreRequest) -> _LRRClientResponse[QueuedMinionJobResponse]:
        url = self.api_context.build_url("/api/database/restore")
        form = aiohttp.FormData()
        form.add_field("file", request.data, filename=request.filename, content_type="application/json")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers, data=form)
        return _process_queued_minion_job_response(content, status)

    async def clear_all_new_flags(self) -> _LRRClientResponse[LanraragiResponse]:
        """
        DELETE /api/database/isnew
        """
        url = self.api_context.build_url("/api/database/isnew")
        status, content = await self.api_context.handle_request(http.HTTPMethod.DELETE, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))


__all__ = [
    "_DatabaseApiClient"
]
