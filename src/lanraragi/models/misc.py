from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse

class GetServerInfoResponse(LanraragiResponse):
    archives_per_page: int = Field(...)
    cache_last_cleared: int = Field(...)
    debug_mode: bool = Field(...)
    has_password: bool = Field(...)
    motd: str = Field(...)
    name: str = Field(...)
    nofun_mode: bool = Field(...)
    server_resizes_images: bool = Field(...)
    server_tracks_progress: bool = Field(...)
    total_archives: int = Field(...)
    total_pages_read: int = Field(...)
    version: str = Field(...)
    version_desc: str = Field(...)
    version_name: str = Field(...)

class GetOpdsCatalogRequest(LanraragiRequest):
    arcid: Optional[str] = Field(None, min_length=40, max_length=40)
    category: Optional[str] = Field(None)

class GetOpdsCatalogResponse(LanraragiResponse):
    result: str = Field(..., description="XML string")

class GetAvailablePluginsRequest(LanraragiRequest):
    type: Literal["login", "metadata", "script", "download", "all"] = Field(...)

class GetAvailablePluginsResponsePlugin(BaseModel):
    author: str = Field(...)
    description: Optional[str] = Field(None)
    icon: Optional[str] = Field(None)
    name: str = Field(...)
    namespace: str = Field(...)
    oneshot_arg: Optional[str] = Field(None)
    parameters: Optional[List[Dict[str, str]]] = Field(None)
    type: Literal["login", "metadata", "script", "download", "all"] = Field(...)
    version: str = Field(...)

class GetAvailablePluginsResponse(LanraragiResponse):
    plugins: List[GetAvailablePluginsResponsePlugin] = Field(...)

class UsePluginRequest(LanraragiRequest):
    plugin: str = Field(..., description="Namespace of the plugin to use.")
    arcid: Optional[str] = Field(None, description="ID of the archive to use the plugin on. This is only mandatory for metadata plugins.")
    arg: Optional[str] = Field(None, description="Optional One-Shot argument to use when executing this Plugin.")

class UsePluginResponse(LanraragiResponse):
    data: Optional[Dict[str, str]] = Field(None)
    type: Literal["login", "metadata", "script"] = Field(...)

class UsePluginAsyncRequest(LanraragiRequest):
    plugin: str = Field(..., description="Namespace of the plugin to use.")
    arcid: Optional[str] = Field(None, description="ID of the archive to use the plugin on. This is only mandatory for metadata plugins.")
    arg: Optional[str] = Field(None, description="Optional One-Shot argument to use when executing this Plugin.")

class UsePluginAsyncResponse(LanraragiResponse):
    job: int = Field(...)

class CleanTempFolderResponse(LanraragiResponse):
    newsize: float = Field(...)

class QueueUrlDownloadRequest(LanraragiRequest):
    url: str = Field(...)
    catid: Optional[str] = Field(None)

class QueueUrlDownloadResponse(LanraragiResponse):
    job: int = Field(...)
    url: str = Field(...)

class RegenerateThumbnailRequest(LanraragiRequest):
    force: Optional[bool] = Field(None)

class RegenerateThumbnailResponse(LanraragiResponse):
    job: int = Field(...)

__all__ = [
    "GetServerInfoResponse",
    "GetOpdsCatalogRequest",
    "GetOpdsCatalogResponse",
    "GetAvailablePluginsRequest",
    "GetAvailablePluginsResponsePlugin",
    "GetAvailablePluginsResponse",
    "UsePluginRequest",
    "UsePluginResponse",
    "UsePluginAsyncRequest",
    "UsePluginAsyncResponse",
    "CleanTempFolderResponse",
    "QueueUrlDownloadRequest",
    "QueueUrlDownloadResponse",
    "RegenerateThumbnailRequest",
    "RegenerateThumbnailResponse",
]
