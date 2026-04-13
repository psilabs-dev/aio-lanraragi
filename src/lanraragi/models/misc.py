from typing import Any, Literal

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
    authenticated_progress: bool = Field(...)
    total_archives: int = Field(...)
    total_pages_read: int = Field(...)
    version: str = Field(...)
    version_desc: str = Field(...)
    version_name: str = Field(...)
    excluded_namespaces: list[str] = Field(default_factory=list)

class GetOpdsCatalogRequest(LanraragiRequest):
    arcid: str | None = Field(None, min_length=40, max_length=40)
    category: str | None = Field(None)

class GetOpdsCatalogResponse(LanraragiResponse):
    result: str = Field(..., description="XML string")

class GetAvailablePluginsRequest(LanraragiRequest):
    type: Literal["login", "metadata", "script", "download", "all"] = Field(...)

class GetAvailablePluginsResponsePlugin(BaseModel):
    author: str = Field(...)
    description: str | None = Field(None)
    icon: str | None = Field(None)
    name: str = Field(...)
    namespace: str = Field(...)
    oneshot_arg: str | None = Field(None)
    parameters: list[dict[str, str]] | None = Field(None)
    type: Literal["login", "metadata", "script", "download", "all"] = Field(...)
    version: str = Field(...)
    hidden: bool = Field(False)
    priority: int = Field(0)
    registry: str | None = Field(None)

class GetAvailablePluginsResponse(LanraragiResponse):
    plugins: list[GetAvailablePluginsResponsePlugin] = Field(...)

class UsePluginRequest(LanraragiRequest):
    plugin: str = Field(..., description="Namespace of the plugin to use.")
    arcid: str | None = Field(None, description="ID of the archive to use the plugin on. This is only mandatory for metadata plugins.")
    arg: str | None = Field(None, description="Optional One-Shot argument to use when executing this Plugin.")

class UsePluginRawResponse(LanraragiResponse):
    operation: str | None = Field(None)
    success: int = Field(...)
    error: str | None = Field(None)
    data: dict[str, Any] | None = Field(None)
    type: Literal["login", "metadata", "script", "download"] | None = Field(None)

class UsePluginResponse(LanraragiResponse):
    data: dict[str, Any] | None = Field(None)
    type: Literal["login", "metadata", "script", "download"] = Field(...)

class UsePluginAsyncRequest(LanraragiRequest):
    plugin: str = Field(..., description="Namespace of the plugin to use.")
    arcid: str | None = Field(None, description="ID of the archive to use the plugin on. This is only mandatory for metadata plugins.")
    arg: str | None = Field(None, description="Optional One-Shot argument to use when executing this Plugin.")

class UsePluginAsyncResponse(LanraragiResponse):
    job: int = Field(...)

class CleanTempFolderResponse(LanraragiResponse):
    newsize: float = Field(...)

class QueueUrlDownloadRequest(LanraragiRequest):
    url: str = Field(...)
    catid: str | None = Field(None)

class QueueUrlDownloadResponse(LanraragiResponse):
    job: int = Field(...)
    url: str = Field(...)

class RegenerateThumbnailRequest(LanraragiRequest):
    force: bool | None = Field(None)

class RegenerateThumbnailResponse(LanraragiResponse):
    job: int = Field(...)

class RegistryConfig(BaseModel):
    id: str = Field(...)
    name: str = Field(...)
    type: Literal["git", "local"] = Field(...)
    provider: Literal["github", "gitlab", "gitea"] | None = Field(None)
    url: str | None = Field(None)
    ref: str | None = Field(None)
    path: str | None = Field(None)

class CreateRegistryRequest(LanraragiRequest):
    name: str = Field(...)
    type: Literal["git", "local"] = Field(...)
    provider: Literal["github", "gitlab", "gitea"] | None = Field(None)
    url: str | None = Field(None)
    ref: str | None = Field(None)
    path: str | None = Field(None)

class CreateRegistryResponse(LanraragiResponse):
    id: str = Field(...)
    registry: RegistryConfig = Field(...)

class UpdateRegistryRequest(LanraragiRequest):
    name: str | None = Field(None)
    type: Literal["git", "local"] | None = Field(None)
    provider: Literal["github", "gitlab", "gitea"] | None = Field(None)
    url: str | None = Field(None)
    ref: str | None = Field(None)
    path: str | None = Field(None)

class UpdateRegistryResponse(LanraragiResponse):
    id: str = Field(...)
    registry: RegistryConfig = Field(...)
    index_cleared: bool = Field(...)

class GetRegistryResponse(LanraragiResponse):
    id: str = Field(...)
    registry: RegistryConfig = Field(...)

class ListRegistriesResponse(LanraragiResponse):
    registries: list[RegistryConfig] = Field(...)

class RefreshRegistryResponse(LanraragiResponse):
    index: dict[str, Any] | None = Field(None)

class UpdatePluginConfigRequest(LanraragiRequest):
    enabled: bool | None = Field(None)
    hidden: bool | None = Field(None)
    priority: int | None = Field(None)

class InstallPluginRequest(LanraragiRequest):
    namespace: str = Field(...)
    registry: str = Field(...)
    force: bool | None = Field(None)

class InstallPluginResponse(LanraragiResponse):
    name: str = Field(...)
    namespace: str = Field(...)
    version: str = Field(...)
    registry: str = Field(...)

__all__ = [
    "GetServerInfoResponse",
    "GetOpdsCatalogRequest",
    "GetOpdsCatalogResponse",
    "GetAvailablePluginsRequest",
    "GetAvailablePluginsResponsePlugin",
    "GetAvailablePluginsResponse",
    "UsePluginRequest",
    "UsePluginRawResponse",
    "UsePluginResponse",
    "UsePluginAsyncRequest",
    "UsePluginAsyncResponse",
    "CleanTempFolderResponse",
    "QueueUrlDownloadRequest",
    "QueueUrlDownloadResponse",
    "RegenerateThumbnailRequest",
    "RegenerateThumbnailResponse",
    "RegistryConfig",
    "CreateRegistryRequest",
    "CreateRegistryResponse",
    "UpdateRegistryRequest",
    "UpdateRegistryResponse",
    "GetRegistryResponse",
    "ListRegistriesResponse",
    "RefreshRegistryResponse",
    "UpdatePluginConfigRequest",
    "InstallPluginRequest",
    "InstallPluginResponse",
]
