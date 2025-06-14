import json
from typing import List
from lanraragi.models.search import GetRandomArchivesResponse, SearchArchiveIndexResponse, SearchArchiveIndexResponseRecord

def _process_search_archive_index_response(content: str) -> SearchArchiveIndexResponse:
    response_j = json.loads(content)
    data = response_j.get('data')
    draw = response_j.get('draw')
    records_filtered = response_j.get('recordsFiltered')
    records_total = response_j.get('recordsTotal')
    records = []
    for record in data:
        arcid = record.get('arcid')
        isnew = record.get('isnew')
        extension = record.get('extension')
        pagecount = record.get('pagecount')
        progress = record.get('progress')
        tags = record.get('tags')
        lastreadtime = record.get('lastreadtime')
        title = record.get('title')
        records.append(SearchArchiveIndexResponseRecord(
            arcid=arcid, isnew=isnew, extension=extension, pagecount=pagecount, 
            progress=progress, tags=tags, lastreadtime=lastreadtime, title=title
        ))
    response = SearchArchiveIndexResponse(
        data=records, draw=draw, records_filtered=records_filtered, records_total=records_total
    )
    return response

def _process_get_random_archives_response(content: str) -> GetRandomArchivesResponse:
    response_j = json.loads(content)
    data = response_j.get('data')
    records_filtered = response_j.get('recordsFiltered')
    records_total = response_j.get('recordsTotal')
    records: List[SearchArchiveIndexResponseRecord] = []
    for record in data:
        arcid = record.get('arcid')
        isnew = record.get('isnew')
        extension = record.get('extension')
        pagecount = record.get('pagecount')
        progress = record.get('progress')
        tags = record.get('tags')
        lastreadtime = record.get('lastreadtime')
        title = record.get('title')
        records.append(SearchArchiveIndexResponseRecord(
            arcid=arcid, isnew=isnew, extension=extension, pagecount=pagecount, 
            progress=progress, tags=tags, lastreadtime=lastreadtime, title=title
        ))
    response = GetRandomArchivesResponse(
        data=records, records_filtered=records_filtered, records_total=records_total
    )
    return response

__all__ = [
    "_process_search_archive_index_response",
    "_process_get_random_archives_response"
]