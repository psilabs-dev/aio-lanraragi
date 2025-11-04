from typing import List, Literal, Optional
from datetime import datetime, timezone
import re
from pydantic import BaseModel

class LogEvent(BaseModel):

    log_time: int
    namespace: str
    severity_level: Literal['debug', 'info', 'warn', 'error']
    message: str

    def __str__(self) -> str:
        ts = datetime.fromtimestamp(self.log_time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return f"[{ts}] [{self.namespace}] [{self.severity_level}] {self.message}"

def parse_lrr_logs(log_content: str, after: Optional[int]=None, before: Optional[int]=None) -> List[LogEvent]:
    """
    Parse logs from lanraragi.log and shinobu.log.
    """
    events: List[LogEvent] = []
    line_re = re.compile(
        r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] "
        r"\[(?P<ns>[^\]]+)\] "
        r"\[(?P<level>debug|info|warn|error)\] "
        r"(?P<msg>.*)$"
    )

    for line in log_content.splitlines():
        match = line_re.match(line)
        if match:
            log_time = int(datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())

            if after and log_time < after:
                continue
            if before and log_time > before:
                continue

            namespace = match.group("ns")
            level = match.group("level")
            message = match.group("msg")
            event = LogEvent(log_time=log_time, namespace=namespace, severity_level=level, message=message)
            events.append(event)
        else:
            if events:
                # Continuation of previous message (multi-line)
                events[-1].message = f"{events[-1].message}\n{line}"

    return events
