import json
from typing import List
from lanraragi.models.misc import GetAvailablePluginsResponse, GetAvailablePluginsResponsePlugin, GetServerInfoResponse, UsePluginResponse


def _process_get_server_info_response(content: str) -> GetServerInfoResponse:
    response_j = json.loads(content)
    archives_per_page = response_j.get("archives_per_page")
    cache_last_cleared = response_j.get("cache_last_cleared")
    debug_mode = response_j.get("debug_mode")
    has_password = response_j.get("has_password")
    motd = response_j.get("motd")
    name = response_j.get("name")
    nofun_mode = response_j.get("nofun_mode")
    server_resizes_images = response_j.get("server_resizes_images")
    server_tracks_progress = response_j.get("server_tracks_progress")
    total_archives = response_j.get("total_archives")
    total_pages_read = response_j.get("total_pages_read")
    version = response_j.get("version")
    version_desc = response_j.get("version_desc")
    version_name = response_j.get("version_name")
    return GetServerInfoResponse(
        archives_per_page=archives_per_page, 
        cache_last_cleared=cache_last_cleared, 
        debug_mode=debug_mode, 
        has_password=has_password, 
        motd=motd, 
        name=name, nofun_mode=nofun_mode, 
        server_resizes_images=server_resizes_images, 
        server_tracks_progress=server_tracks_progress, 
        total_archives=total_archives, 
        total_pages_read=total_pages_read, 
        version=version, 
        version_desc=version_desc, 
        version_name=version_name
    )

def _handle_get_available_plugins_response(content: str) -> GetAvailablePluginsResponse:
    response_j = json.loads(content)
    plugins: List[GetAvailablePluginsResponsePlugin] = []
    for plugin in response_j:
        author = plugin.get("author")
        description = plugin.get("description")
        icon = plugin.get("icon")
        name = plugin.get("name")
        namespace = plugin.get("namespace")
        oneshot_arg = plugin.get("oneshot_arg")
        parameters = plugin.get("parameters")
        type = plugin.get("type")
        version = plugin.get("version")
        plugins.append(GetAvailablePluginsResponsePlugin(
            author=author, 
            description=description, 
            icon=icon, 
            name=name, 
            namespace=namespace, 
            oneshot_arg=oneshot_arg, 
            parameters=parameters, 
            type=type, 
            version=version
        ))
    response = GetAvailablePluginsResponse(plugins=plugins)
    return response

def _handle_use_plugin_response(content: str) -> UsePluginResponse:
    response_j = json.loads(content)
    data = response_j.get("data")
    type = response_j.get("type")
    return UsePluginResponse(data=data, type=type)

__all__ = [
    "_process_get_server_info_response",
    "_handle_get_available_plugins_response",
    "_handle_use_plugin_response"
]
