
import json
from pathlib import Path

from aio_lanraragi_tests.registries.base import AbstractRegistry


class LocalRegistry(AbstractRegistry):

    def __init__(self, name: str, root: Path):
        super().__init__()
        self.name = name
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def registry_json_path(self) -> Path:
        return self._root / "registry.json"

    def generate_manifest(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for (namespace, version), content in self._artifacts.items():
            relpath = self._plugins[namespace]["versions"][version]["artifact"]
            artifact_path = self._root / relpath
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_bytes(content)
        self.registry_json_path.write_text(json.dumps(self.manifest()), encoding="utf-8")
