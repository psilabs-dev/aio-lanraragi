from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse

class GetAllArchivesResponseRecord(BaseModel):
    arcid: str = Field(..., min_length=40, max_length=40)
    isnew: bool = Field(...)
    extension: str = Field(...)
    tags: str | None = Field(None)
    lastreadtime: int | None = Field(None)
    pagecount: int | None = Field(None)
    progress: int | None = Field(None)
    title: str = Field(...)

class GetAllArchivesResponse(LanraragiResponse):
    data: list[GetAllArchivesResponseRecord] = Field(...)

class GetUntaggedArchivesResponse(LanraragiResponse):
    data: list[str] = Field(...)

class GetArchiveMetadataRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class GetArchiveMetadataResponse(LanraragiResponse):
    arcid: str = Field(..., min_length=40, max_length=40)
    isnew: bool = Field(...)
    pagecount: int = Field(...)
    progress: int = Field(...)
    tags: str = Field(...)
    summary: str = Field(...)
    lastreadtime: int = Field(...)
    title: str = Field(...)
    filename: str = Field(...)
    extension: str = Field(...)

class GetArchiveCategoriesRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class GetArchiveCategoriesCatRecord(BaseModel):
    archives: list[str] = Field(...)
    category_id: str = Field(..., validation_alias="id")
    name: str = Field(...)
    pinned: bool = Field(...)
    search: str = Field(...)

class GetArchiveCategoriesResponse(LanraragiResponse):
    categories: list[GetArchiveCategoriesCatRecord] = Field(...)

class GetArchiveTankoubonsRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class GetArchiveTankoubonsResponse(LanraragiResponse):
    tankoubons: list[str] = Field(...)

class GetArchiveThumbnailRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    page: int | None = Field(None)
    nofallback: bool | None = Field(None)

class GetArchiveThumbnailResponse(LanraragiResponse):
    job: int | None = Field(None)
    content: bytes | None = Field(None)
    content_type: str | None = Field(None)

class QueueArchiveThumbnailExtractionRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    force: bool | None = Field(None)

class QueueArchiveThumbnailExtractionResponse(LanraragiResponse):
    job: int | None = Field(...)
    message: str | None = Field(...)

class DownloadArchiveRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class DownloadArchiveResponse(LanraragiResponse):
    data: bytes = Field(...)

class ExtractArchiveRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    force: bool | None = Field(None)

class ExtractArchiveResponse(LanraragiResponse):
    job: int | None = Field(None)
    pages: list[str] = Field([])

class ClearNewArchiveFlagRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class ClearNewArchiveFlagResponse(LanraragiResponse):
    arcid: str = Field(..., min_length=40, max_length=40)

class UpdateReadingProgressionRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    page: int = Field(...)

class UpdateReadingProgressionResponse(LanraragiResponse):
    arcid: str = Field(..., min_length=40, max_length=40)
    page: int = Field(...)
    lastreadtime: int = Field(...)

class UploadArchiveRequest(LanraragiRequest):
    file: bytes = Field(...)
    filename: str = Field(...)
    title: str | None = Field(None)
    tags: str | None = Field(None)
    summary: str | None = Field(None)
    category_id: str | None = Field(None)
    file_checksum: str | None = Field(None)

class UploadArchiveResponse(LanraragiResponse):
    arcid: str = Field(..., min_length=40, max_length=40)
    filename: str | None = Field(None)

class UpdateArchiveThumbnailRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    page: int = Field(...)

class UpdateArchiveThumbnailResponse(LanraragiResponse):
    new_thumbnail: str = Field(...)

class UpdateArchiveMetadataRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    title: str | None = Field(None)
    tags: str | None = Field(None)
    summary: str | None = Field(None)

class DeleteArchiveRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class DeleteArchiveResponse(LanraragiResponse):
    arcid: str = Field(..., min_length=40, max_length=40)
    filename: str | None = Field(None)

# <<<<< ARCHIVE <<<<<

__all__ = [
    "GetAllArchivesResponseRecord",
    "GetAllArchivesResponse",
    "GetUntaggedArchivesResponse",
    "GetArchiveMetadataRequest",
    "GetArchiveMetadataResponse",
    "GetArchiveCategoriesRequest",
    "GetArchiveCategoriesCatRecord",
    "GetArchiveCategoriesResponse",
    "GetArchiveTankoubonsRequest",
    "GetArchiveTankoubonsResponse",
    "GetArchiveThumbnailRequest",
    "GetArchiveThumbnailResponse",
    "QueueArchiveThumbnailExtractionRequest",
    "QueueArchiveThumbnailExtractionResponse",
    "DownloadArchiveRequest",
    "DownloadArchiveResponse",
    "ExtractArchiveRequest",
    "ExtractArchiveResponse",
    "ClearNewArchiveFlagRequest",
    "ClearNewArchiveFlagResponse",
    "UpdateReadingProgressionRequest",
    "UpdateReadingProgressionResponse",
    "UploadArchiveRequest",
    "UploadArchiveResponse",
    "UpdateArchiveThumbnailRequest",
    "UpdateArchiveThumbnailResponse",
    "UpdateArchiveMetadataRequest",
    "DeleteArchiveRequest",
    "DeleteArchiveResponse",
]
