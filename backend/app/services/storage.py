import hashlib
import os
from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    def path(self, key: str) -> Path: ...
    def metadata(self, key: str) -> dict: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        path = (self.root / key).resolve()
        if not key or path == self.root or not path.is_relative_to(self.root):
            raise ValueError("Invalid storage key")
        return path

    def metadata(self, key):
        path = self.path(key)
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        return {"storage_key": key, "file_size": path.stat().st_size, "checksum": digest.hexdigest()}

    def sync_directory(self, key):
        if os.name != "nt":
            directory = self.root if key == "." else self.path(key)
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def usage(self):
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def save_upload(self, source, key, max_bytes):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with path.open("xb") as output:
                while chunk := source.read(65536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("FILE_TOO_LARGE")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            self.sync_directory(str(path.parent.relative_to(self.root)))
            self.sync_directory("jobs")
            self.sync_directory(".")
            if not size:
                raise ValueError("EMPTY_FILE")
            return self.metadata(key)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def delete(self, key):
        self.path(key).unlink(missing_ok=True)
