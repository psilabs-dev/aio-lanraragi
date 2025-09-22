from typing import List, Optional
from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse

class GetAllArchivesResponseRecord(BaseModel):
    arcid: str = Field(..., min_length=40, max_length=40)
    isnew: bool = Field(...)
    extension: str = Field(...)
    tags: Optional[str] = Field(None)
    lastreadtime: Optional[int] = Field(None)
    pagecount: Optional[int] = Field(None)
    progress: Optional[int] = Field(None)
    title: str = Field(...)

class GetAllArchivesResponse(LanraragiResponse):
    data: List[GetAllArchivesResponseRecord] = Field(...)

class GetUntaggedArchivesResponse(LanraragiResponse):
    data: List[str] = Field(...)

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
    archives: List[str] = Field(...)
    category_id: str = Field(...)
    name: str = Field(...)
    pinned: bool = Field(...)
    search: str = Field(...)

class GetArchiveCategoriesResponse(LanraragiResponse):
    categories: List[GetArchiveCategoriesCatRecord] = Field(...)

class GetArchiveTankoubonsRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class GetArchiveTankoubonsResponse(LanraragiResponse):
    tankoubons: List[str] = Field(...)

class GetArchiveThumbnailRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    page: Optional[int] = Field(None)
    nofallback: Optional[bool] = Field(None)

class GetArchiveThumbnailResponse(LanraragiResponse):
    job: Optional[int] = Field(None)
    content: Optional[bytes] = Field(None)
    content_type: Optional[str] = Field(None)

class QueueArchiveThumbnailExtractionRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    force: Optional[bool] = Field(None)

class QueueArchiveThumbnailExtractionResponse(LanraragiResponse):
    job: Optional[int] = Field(...)
    message: Optional[str] = Field(...)

class DownloadArchiveRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class DownloadArchiveResponse(LanraragiResponse):
    data: bytes = Field(...)

class ExtractArchiveRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    force: Optional[bool] = Field(None)

class ExtractArchiveResponse(LanraragiResponse):
    job: Optional[int] = Field(None)
    pages: List[str] = Field([])

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
    title: Optional[str] = Field(None)
    tags: Optional[str] = Field(None)
    summary: Optional[str] = Field(None)
    category_id: Optional[str] = Field(None)
    file_checksum: Optional[str] = Field(None)

class UploadArchiveResponse(LanraragiResponse):
    arcid: str = Field(..., min_length=40, max_length=40)

class UpdateArchiveThumbnailRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    page: int = Field(...)

class UpdateArchiveThumbnailResponse(LanraragiResponse):
    new_thumbnail: str = Field(...)

class UpdateArchiveMetadataRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)
    title: Optional[str] = Field(None)
    tags: Optional[str] = Field(None)
    summary: Optional[str] = Field(None)

class DeleteArchiveRequest(LanraragiRequest):
    arcid: str = Field(..., min_length=40, max_length=40)

class DeleteArchiveResponse(LanraragiResponse):
    arcid: str = Field(..., min_length=40, max_length=40)
    filename: Optional[str] = Field(None)

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