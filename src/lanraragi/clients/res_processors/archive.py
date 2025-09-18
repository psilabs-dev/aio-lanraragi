import json
from typing import List
from lanraragi.models.archive import GetAllArchivesResponse, GetAllArchivesResponseRecord, GetArchiveCategoriesCatRecord, GetArchiveCategoriesResponse, GetArchiveMetadataResponse, GetArchiveThumbnailResponse


def _process_get_all_archives_response(content: str) -> GetAllArchivesResponse:
    response_j = json.loads(content) # note: this is already a list.
    records: List[GetAllArchivesResponseRecord] = []
    for record in response_j:
        arcid = record.get('arcid')
        isnew = record.get('isnew')
        extension = record.get('extension')
        pagecount = record.get('pagecount')
        progress = record.get('progress')
        tags = record.get('tags')
        lastreadtime = record.get('lastreadtime')
        title = record.get('title')
        records.append(GetAllArchivesResponseRecord(
            arcid=arcid, isnew=isnew, extension=extension, pagecount=pagecount, progress=progress, tags=tags, lastreadtime=lastreadtime, title=title
        ))
    response = GetAllArchivesResponse(
        data=records
    )
    return response

def _process_get_archive_metadata_response(content: str) -> GetArchiveMetadataResponse:
    response_j = json.loads(content)
    arcid = response_j.get("arcid")
    isnew = response_j.get("isnew")
    pagecount = response_j.get("pagecount")
    progress = response_j.get("progress")
    tags = response_j.get("tags")
    summary = response_j.get("summary")
    lastreadtime = response_j.get("lastreadtime")
    title = response_j.get("title")
    filename = response_j.get("filename")
    extension = response_j.get("extension")
    return GetArchiveMetadataResponse(
        arcid=arcid, isnew=isnew, pagecount=pagecount,
        progress=progress, tags=tags, lastreadtime=lastreadtime,
        title=title, filename=filename, extension=extension,
        summary=summary
    )

def _process_get_archive_categories_response(content: str) -> GetArchiveCategoriesResponse:
    response_j = json.loads(content)
    categories: List[GetArchiveCategoriesCatRecord] = []
    for category in response_j:
        archives = category.get("archives")
        id = category.get("id")
        name = category.get("name")
        pinned = category.get("pinned") == "1"
        search = category.get("search")
        categories.append(GetArchiveCategoriesCatRecord(archives=archives, category_id=id, name=name, pinned=pinned, search=search))
    response = GetArchiveCategoriesResponse(categories=categories)
    return response

def _process_get_archive_thumbnail_response(content: str, status: int) -> GetArchiveThumbnailResponse:
    """
    Handle all successful status codes (200 or 202).
    """
    if status == 200:
        return GetArchiveThumbnailResponse(content=content, content_type="image/jpeg")
    elif status == 202:
        response_j = json.loads(content)
        job = response_j.get("job")
        return GetArchiveThumbnailResponse(job=job, content=None, content_type=None)

__all__ = [
    "_process_get_all_archives_response",
    "_process_get_archive_metadata_response",
    "_process_get_archive_categories_response",
    "_process_get_archive_thumbnail_response"
]
