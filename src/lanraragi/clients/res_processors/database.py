
import json
from typing import List
from lanraragi.models.database import GetDatabaseStatsResponse, GetDatabaseStatsResponseTag


def process_get_database_stats_response(content: str) -> GetDatabaseStatsResponse:
    response_j = json.loads(content)
    data = response_j.get("data")
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
