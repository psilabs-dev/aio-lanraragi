
import abc
import hashlib

REGISTRY_SCHEMA_VERSION = 1


class AbstractRegistry(abc.ABC):

    def __init__(self, generated_at: str = "2026-01-01T00:00:00Z"):
        self._generated_at = generated_at
        # namespace -> {"type": str, "versions": {version: version_record}}
        self._plugins: dict[str, dict] = {}
        # (namespace, version) -> artifact bytes staged for generate_manifest()
        self._artifacts: dict[tuple[str, str], bytes] = {}

    def add_plugin(
        self,
        namespace: str,
        plugin_type: str,
        version: str,
        *,
        name: str,
        author: str,
        description: str,
        artifact_content: bytes | str | None = None,
        artifact_relpath: str | None = None,
        published_at: str | None = None,
        sha256: str | None = None,
    ) -> None:
        artifact = artifact_relpath or f"artifacts/{namespace}/{version}/{namespace}.pm"
        if artifact_content is not None:
            content = artifact_content.encode("utf-8") if isinstance(artifact_content, str) else artifact_content
            self._artifacts[(namespace, version)] = content
            if sha256 is None:
                sha256 = hashlib.sha256(content).hexdigest()
        elif sha256 is None:
            raise ValueError("sha256 must be provided when artifact_content is None")

        record = self._plugins.setdefault(namespace, {"type": plugin_type, "versions": {}})
        record["type"] = plugin_type
        record["versions"][version] = {
            "version": version,
            "name": name,
            "author": author,
            "description": description,
            "artifact": artifact,
            "sha256": sha256,
            "published_at": published_at or self._generated_at,
        }

    def remove_plugin(self, namespace: str, version: str | None = None) -> None:
        if namespace not in self._plugins:
            return
        if version is None:
            del self._plugins[namespace]
            self._artifacts = {k: v for k, v in self._artifacts.items() if k[0] != namespace}
            return
        self._plugins[namespace]["versions"].pop(version, None)
        self._artifacts.pop((namespace, version), None)
        if not self._plugins[namespace]["versions"]:
            del self._plugins[namespace]

    def manifest(self) -> dict:
        return {
            "version": REGISTRY_SCHEMA_VERSION,
            "generated_at": self._generated_at,
            "plugins": {
                namespace: {
                    "namespace": namespace,
                    "type": record["type"],
                    "versions": record["versions"],
                }
                for namespace, record in self._plugins.items()
            },
        }

    @abc.abstractmethod
    def generate_manifest(self) -> None:
        """Flush the manifest and any staged artifacts to the registry's backing store."""
