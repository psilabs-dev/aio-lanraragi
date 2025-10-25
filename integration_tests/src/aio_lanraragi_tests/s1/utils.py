import importlib.resources
import json
from typing import Dict, List

from aio_lanraragi_tests.s1.models import S1ArchiveInfo, S1CategoryInfo

def load_dataset() -> Dict[str, List]:
    data = json.loads(
        importlib.resources.files("aio_lanraragi_tests").joinpath("resources/s1/dataset.json").read_text(encoding='utf-8')
    )

    archives_data = data.get("archives", [])
    categories_data = data.get("categories", [])

    archives: List[S1ArchiveInfo] = [S1ArchiveInfo(**a) for a in archives_data]
    categories: List[S1CategoryInfo] = [S1CategoryInfo(**c) for c in categories_data]

    return {"archives": archives, "categories": categories}
