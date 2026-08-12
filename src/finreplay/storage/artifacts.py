"""Atomic, content-addressed local storage for uncommitted upstream responses."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from finreplay.adapters import RawArtifact


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    sha256: str
    path: Path
    bytes: int
    created: bool


class ContentAddressedStore:
    """Write raw bytes once under their SHA-256 without trusting source filenames."""

    def __init__(self, root: Path) -> None:
        root = root.expanduser().resolve()
        if root == Path(root.anchor):
            raise ValueError("content store root must not be a filesystem root")
        self.root = root

    def put(self, artifact: RawArtifact) -> StoredArtifact:
        destination = self.root / artifact.sha256[:2] / f"{artifact.sha256}.bin"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify_existing(destination, artifact.sha256, len(artifact.content))
            return StoredArtifact(
                sha256=artifact.sha256,
                path=destination,
                bytes=len(artifact.content),
                created=False,
            )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{artifact.sha256}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(artifact.content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        self._verify_existing(destination, artifact.sha256, len(artifact.content))
        return StoredArtifact(
            sha256=artifact.sha256,
            path=destination,
            bytes=len(artifact.content),
            created=True,
        )

    @staticmethod
    def _verify_existing(path: Path, expected_hash: str, expected_bytes: int) -> None:
        content = path.read_bytes()
        if len(content) != expected_bytes:
            raise RuntimeError(f"content-store size mismatch for {path}")
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError(f"content-store hash mismatch for {path}")

