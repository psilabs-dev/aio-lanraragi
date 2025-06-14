import json
from typing import List
from lanraragi.models.database import (
    GetDatabaseBackupArchiveRecord,
    GetDatabaseBackupCategoryRecord,
    GetDatabaseBackupResponse,
    GetDatabaseBackupTankoubonRecord,
    GetDatabaseStatsResponse,
    GetDatabaseStatsResponseTag
)

def _process_get_database_stats_response(content: str) -> GetDatabaseStatsResponse:
    data = json.loads(content) # note: this is a list of tags, not a dictionary
    tags: List[GetDatabaseStatsResponseTag] = []
    for tag in data:
        namespace = tag.get("namespace")
        text = tag.get("text")
        weight = tag.get("weight")
        tags.append(GetDatabaseStatsResponseTag(namespace=namespace, text=text, weight=weight))
    response = GetDatabaseStatsResponse(
        data=tags
    )
    return response

def _process_get_database_backup_response(content: str) -> GetDatabaseBackupResponse:
    response_j = json.loads(content)
    archive_records: List[GetDatabaseBackupArchiveRecord] = []
    if "archives" in response_j:
        for arc_record in response_j.get("archives"):
            archive_records.append(GetDatabaseBackupArchiveRecord(
                arcid=arc_record.get("arcid"),
                title=arc_record.get("title"),
                tags=arc_record.get("tags"),
                summary=arc_record.get("summary"),
                thumbhash=arc_record.get("thumbhash"),
                filename=arc_record.get("filename")
            ))
    if "categories" in response_j:
        category_records: List[GetDatabaseBackupCategoryRecord] = []
        for cat_record in response_j.get("categories"):
            category_records.append(GetDatabaseBackupCategoryRecord(
                archives=cat_record.get("archives"),
                category_id=cat_record.get("catid"),
                name=cat_record.get("name"),
                search=cat_record.get("search")
            ))
    tankoubon_records: List[GetDatabaseBackupTankoubonRecord] = []
    if "tankoubons" in response_j:
        for tank_record in response_j.get("tankoubons"):
            tankoubon_records.append(GetDatabaseBackupTankoubonRecord(
                tankid=tank_record.get("tankid"),
                name=tank_record.get("name"),
                archives=tank_record.get("archives")
            ))
    return GetDatabaseBackupResponse(archives=archive_records, categories=category_records, tankoubons=tankoubon_records)

__all__ = [
    "_process_get_database_stats_response",
    "_process_get_database_backup_response"
]