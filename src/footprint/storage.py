"""Local stand-in for S3: keys are strings, objects are files under a root directory.

The interface is deliberately the shape of boto3's, so swapping in a real bucket
means replacing the four method bodies with `put_object` / `get_object` calls.
"""

from __future__ import annotations

import json
from pathlib import Path


class ObjectExistsError(RuntimeError):
    """Raised when writing over an existing object in an immutable bucket."""


class LocalS3:
    """A bucket. `immutable=True` refuses overwrites, which is how the raw zone behaves."""

    def __init__(self, root: Path, name: str = "local-bucket", immutable: bool = False) -> None:
        self.root = Path(root)
        self.name = name
        self.immutable = immutable

    def _path(self, key: str) -> Path:
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def put_json(self, key: str, obj) -> str:
        """Write `obj` as JSON at `key`. Returns the key, like S3 does."""
        if self.immutable and self.exists(key):
            raise ObjectExistsError(f"{self.name}/{key} already exists and the bucket is immutable")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
        return key

    def get_json(self, key: str):
        return json.loads(self._path(key).read_text(encoding="utf-8"))

    def list_keys(self, prefix: str = "") -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(
            p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file()
        )

    def local_path(self, key: str) -> Path:
        """Escape hatch: duckdb reads and writes files, not S3 objects."""
        return self._path(key)
