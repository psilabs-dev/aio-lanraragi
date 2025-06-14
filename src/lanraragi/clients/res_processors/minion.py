import json
from lanraragi.models.minion import GetMinionJobDetailResponse, GetMinionJobDetailResponseResult


def _process_get_minion_job_detail_response(content: str) -> GetMinionJobDetailResponse:
    response_j = json.loads(content)
    id = response_j.get("id")
    args = response_j.get("args")
    attempts = response_j.get("attempts")
    children = response_j.get("children")
    created = response_j.get("created")
    delayed = response_j.get("delayed")
    expires = response_j.get("expires")
    finished = response_j.get("finished")
    lax = response_j.get("lax")
    notes = response_j.get("notes")
    parents = response_j.get("parents")
    priority = response_j.get("priority")
    queue = response_j.get("queue")
    result = response_j.get("result")
    retried = response_j.get("retried")
    retries = response_j.get("retries")
    started = response_j.get("started")
    state = response_j.get("state")
    task = response_j.get("task")
    time = response_j.get("time")
    worker = response_j.get("worker")
    result_j = response_j.get("result")
    result = GetMinionJobDetailResponseResult(
        success=result_j.get("success"),
        id=result_j.get("id"),
        message=result_j.get("message"),
        url=result_j.get("url"),
        category=result_j.get("category"),
        title=result_j.get("title"),
        error=result_j.get("error"),
        data=result_j.get("data"),
        errors=result_j.get("errors")
    ) if result_j else None
    return GetMinionJobDetailResponse(
        id=id,
        args=args,
        attempts=attempts,
        children=children,
        created=created,
        delayed=delayed,
        expires=expires,
        finished=finished,
        lax=lax,
        notes=notes,
        parents=parents,
        priority=priority,
        queue=queue,
        result=result,
        retried=retried,
        retries=retries,
        started=started,
        state=state,
        task=task,
        time=time,
        worker=worker
    )

__all__ = [
    "_process_get_minion_job_detail_response"
]