import json

from pydantic import TypeAdapter

from lanraragi.models.tankoubon import (
    GetAllTankoubonsResponse,
    GetTankoubonResponse,
    TankoubonArchiveRecord,
    TankoubonFullDataRecord,
    TankoubonRecord,
)

_all_tankoubons_adapter = TypeAdapter(GetAllTankoubonsResponse)

def _handle_get_all_tankoubons_response(content: str) -> GetAllTankoubonsResponse:
    return _all_tankoubons_adapter.validate_json(content)

def _handle_get_tankoubon_response(content: str, is_full_data: bool) -> GetTankoubonResponse:
    response_j = json.loads(content)
    result_j = response_j.get("result")

    tank_id = result_j.get("id")
    name = result_j.get("name")
    summary = result_j.get("summary")
    tags = result_j.get("tags")
    archives = result_j.get("archives")
    filtered = response_j.get("filtered")
    total = response_j.get("total")

    if not is_full_data:
        response = GetTankoubonResponse(
            filtered=filtered,
            total=total,
            result=TankoubonRecord(
                archives=archives,
                id=tank_id,
                name=name,
                summary=summary,
                tags=tags
            )
        )
        return response

    # handle full data response
    full_data_records: list[TankoubonArchiveRecord] = []
    for record in result_j.get("full_data"):
        full_data_records.append(TankoubonArchiveRecord(
            arcid=record.get("arcid"),
            extension=record.get("extension"),
            isnew=record.get("isnew"),
            lastreadtime=record.get("lastreadtime"),
            pagecount=record.get("pagecount"),
            progress=record.get("progress"),
            tags=record.get("tags"),
            title=record.get("title")
        ))

    response = GetTankoubonResponse(
        filtered=filtered,
        total=total,
        result=TankoubonFullDataRecord(
            archives=archives,
            id=tank_id,
            name=name,
            summary=summary,
            tags=tags,
            full_data=full_data_records
        )
    )

__all__ = [
    "_handle_get_all_tankoubons_response",
    "_handle_get_tankoubon_response"
]
