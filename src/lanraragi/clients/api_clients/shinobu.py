

import http
import json
from lanraragi.clients.api_clients.base import ApiClient
from lanraragi.clients.utils import build_err_response
from lanraragi.models.generics import LRRClientResponse
from lanraragi.models.base import LanraragiResponse
from lanraragi.models.shinobu import GetShinobuStatusResponse, RestartShinobuResponse


class ShinobuApiClient(ApiClient):
    async def get_shinobu_status(self) -> LRRClientResponse[GetShinobuStatusResponse]:
        """
        GET /api/shinobu
        """
        url = self.api_context.build_url("/api/shinobu")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.api_context.headers)
        if status == 200:
            response_j = json.loads(content)
            isalive = response_j.get("isalive")
            pid = response_j.get("pid")
            return (GetShinobuStatusResponse(isalive=isalive, pid=pid), None)
        return (None, build_err_response(content, status))

    async def stop_shinobu(self) -> LRRClientResponse[LanraragiResponse]:
        """
        POST /api/shinobu/stop
        """
        url = self.api_context.build_url("/api/shinobu/stop")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.api_context.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, build_err_response(content, status))

    async def restart_shinobu(self) -> LRRClientResponse[RestartShinobuResponse]:
        """
        POST /api/shinobu/restart
        """
        url = self.api_context.build_url("/api/shinobu/restart")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.api_context.headers)
        if status == 200:
            response_j = json.loads(content)
            new_pid = response_j.get("new_pid")
            return (RestartShinobuResponse(new_pid=new_pid), None)
        return (None, build_err_response(content, status))