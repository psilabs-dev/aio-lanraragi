from pydantic import TypeAdapter

from lanraragi.models.misc import (
    GetAvailablePluginsResponse,
    GetAvailablePluginsResponsePlugin,
    GetServerInfoResponse,
    UsePluginResponse,
)

_available_plugins_adapter = TypeAdapter(list[GetAvailablePluginsResponsePlugin])

def _process_get_server_info_response(content: str) -> GetServerInfoResponse:
    return GetServerInfoResponse.model_validate_json(content)

def _handle_get_available_plugins_response(content: str) -> GetAvailablePluginsResponse:
    return GetAvailablePluginsResponse(plugins=_available_plugins_adapter.validate_json(content))

def _handle_use_plugin_response(content: str) -> UsePluginResponse:
    return UsePluginResponse.model_validate_json(content)

__all__ = [
    "_process_get_server_info_response",
    "_handle_get_available_plugins_response",
    "_handle_use_plugin_response"
]
