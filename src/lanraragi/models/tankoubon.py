from pydantic import BaseModel, Field

from lanraragi.models.base import LanraragiRequest, LanraragiResponse

class TankoubonRecord(BaseModel):
    archives: list[str] = Field(...)
    tank_id: str = Field(..., validation_alias="id")
    name: str = Field(...)
    summary: str = Field(...)
    tags: str = Field(...)

class TankoubonArchiveRecord(BaseModel):
    arcid: str = Field(..., min_length=40, max_length=40)
    extension: str = Field(...)
    isnew: bool = Field(...)
    lastreadtime: int = Field(...)
    pagecount: int = Field(...)
    progress: int = Field(...)
    tags: str = Field(...)
    title: str = Field(...)

class TankoubonFullDataRecord(TankoubonRecord):
    full_data: list[TankoubonArchiveRecord] = Field(...)

class GetAllTankoubonsRequest(LanraragiRequest):
    page: int = Field(..., description="The page of the list of Tankoubons.")

class GetAllTankoubonsResponse(LanraragiResponse):
    result: list[TankoubonRecord] = Field(...)
    filtered: int = Field(...)
    total: int = Field(...)

class GetTankoubonRequest(LanraragiRequest):
    tank_id: str = Field(..., description="The ID of the Tankoubon.")
    include_full_data: str | None = Field(None, description="If set in 1, it appends a full_data array with Archive objects.")
    page: str | None = Field(None, description="The page of the list of Archives.")

class GetTankoubonResponse(LanraragiResponse):
    result: TankoubonRecord = Field(...) # can be TankoubonRecord or TankoubonFullDataRecord.
    filtered: int = Field(...)
    total: int = Field(...)

class CreateTankoubonRequest(LanraragiRequest):
    name: str = Field(...)

class CreateTankoubonResponse(LanraragiResponse):
    tank_id: str = Field(...)

class TankoubonMetadata(BaseModel):
    name: str | None = Field(None, description="The name of the tankoubon")
    summary: str | None = Field(None, description="The summary of the tankoubon") 
    tags: str | None = Field(None, description="The tags of the tankoubon")

class UpdateTankoubonRequest(LanraragiRequest):
    tank_id: str = Field(...)
    archives: list[str] | None = Field(None)
    metadata: TankoubonMetadata | None = Field(None)

class UpdateTankoubonResponse(LanraragiResponse):
    success_message: str | None = Field(None)

class AddArchiveToTankoubonRequest(LanraragiRequest):
    tank_id: str = Field(...)
    arcid: str = Field(..., min_length=40, max_length=40)

class AddArchiveToTankoubonResponse(LanraragiResponse):
    success_message: str | None = Field(None)

class RemoveArchiveFromTankoubonRequest(LanraragiRequest):
    tank_id: str = Field(...)
    arcid: str = Field(..., min_length=40, max_length=40)

class RemoveArchiveFromTankoubonResponse(LanraragiResponse):
    success_message: str | None = Field(None)

class DeleteTankoubonRequest(LanraragiRequest):
    tank_id: str = Field(...)

class DeleteTankoubonResponse(LanraragiResponse):
    success_message: str | None = Field(None)

__all__ = [
    "TankoubonRecord",
    "TankoubonArchiveRecord",
    "TankoubonFullDataRecord",
    "TankoubonMetadata",
    "GetAllTankoubonsRequest",
    "GetAllTankoubonsResponse",
    "GetTankoubonRequest",
    "GetTankoubonResponse",
    "CreateTankoubonRequest",
    "CreateTankoubonResponse",
    "UpdateTankoubonRequest",
    "UpdateTankoubonResponse",
    "AddArchiveToTankoubonRequest",
    "AddArchiveToTankoubonResponse",
    "RemoveArchiveFromTankoubonRequest",
    "RemoveArchiveFromTankoubonResponse",
    "DeleteTankoubonRequest",
    "DeleteTankoubonResponse",
]
