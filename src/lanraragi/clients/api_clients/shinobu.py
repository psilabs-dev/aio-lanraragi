import http
import json
from lanraragi.clients.api_clients.base import _ApiClient
from lanraragi.clients.utils import _build_err_response
from lanraragi.models.generics import _LRRClientResponse
from lanraragi.models.base import LanraragiResponse
from lanraragi.models.shinobu import GetShinobuStatusResponse, RestartShinobuResponse


class _ShinobuApiClient(_ApiClient):
    async def get_shinobu_status(self) -> _LRRClientResponse[GetShinobuStatusResponse]:
        """
        GET /api/shinobu
        """
        url = self.api_context.build_url("/api/shinobu")
        status, content = await self.api_context.handle_request(http.HTTPMethod.GET, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            is_alive = response_j["is_alive"]
            if isinstance(is_alive, int):
                is_alive = is_alive == 1
            else:
                raise TypeError(f"is_alive is not a boolean: {is_alive}")
            pid = response_j.get("pid")
            return (GetShinobuStatusResponse(is_alive=is_alive, pid=pid), None)
        return (None, _build_err_response(content, status))

    async def stop_shinobu(self) -> _LRRClientResponse[LanraragiResponse]:
        """
        POST /api/shinobu/stop
        """
        url = self.api_context.build_url("/api/shinobu/stop")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers)
        if status == 200:
            return (LanraragiResponse(), None)
        return (None, _build_err_response(content, status))

    async def restart_shinobu(self) -> _LRRClientResponse[RestartShinobuResponse]:
        """
        POST /api/shinobu/restart
        """
        url = self.api_context.build_url("/api/shinobu/restart")
        status, content = await self.api_context.handle_request(http.HTTPMethod.POST, url, self.headers)
        if status == 200:
            response_j = json.loads(content)
            new_pid = response_j["new_pid"]
            return (RestartShinobuResponse(new_pid=new_pid), None)
        return (None, _build_err_response(content, status))
    
__all__ = [
    "_ShinobuApiClient"
]