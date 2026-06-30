from pydantic import TypeAdapter

from lanraragi.models.misc import (
    GetAvailablePluginsResponse,
    GetAvailablePluginsResponsePlugin,
    GetServerInfoResponse,
    GetServerStatusResponse,
)

_available_plugins_adapter = TypeAdapter(list[GetAvailablePluginsResponsePlugin])

def _process_get_server_info_response(content: str) -> GetServerInfoResponse:
    return GetServerInfoResponse.model_validate_json(content)

def _process_get_server_status_response(content: str) -> GetServerStatusResponse:
    return GetServerStatusResponse.model_validate_json(content)

def _handle_get_available_plugins_response(content: str) -> GetAvailablePluginsResponse:
    return GetAvailablePluginsResponse(plugins=_available_plugins_adapter.validate_json(content))

__all__ = [
    "_process_get_server_info_response",
    "_process_get_server_status_response",
    "_handle_get_available_plugins_response",
]
