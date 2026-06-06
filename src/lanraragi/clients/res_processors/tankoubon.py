from pydantic import TypeAdapter

from lanraragi.models.tankoubon import (
    GetAllTankoubonsResponse,
    GetTankoubonFullResponse,
    GetTankoubonResponse,
)

_all_tankoubons_adapter = TypeAdapter(GetAllTankoubonsResponse)
_tankoubon_adapter = TypeAdapter(GetTankoubonResponse)
_tankoubon_full_adapter = TypeAdapter(GetTankoubonFullResponse)

def _handle_get_all_tankoubons_response(content: str) -> GetAllTankoubonsResponse:
    return _all_tankoubons_adapter.validate_json(content)

def _handle_get_tankoubon_response(content: str) -> GetTankoubonResponse:
    return _tankoubon_adapter.validate_json(content)

def _handle_get_tankoubon_full_response(content: str) -> GetTankoubonFullResponse:
    return _tankoubon_full_adapter.validate_json(content)

__all__ = [
    "_handle_get_all_tankoubons_response",
    "_handle_get_tankoubon_response",
    "_handle_get_tankoubon_full_response",
]
