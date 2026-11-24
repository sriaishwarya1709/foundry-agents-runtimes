import json
import sqlite3
from pathlib import Path
from typing import Protocol

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient


class Store(Protocol):
    def read(self, key: str) -> dict | None: ...
    def create(self, key: str, value: dict) -> bool: ...


class BlobStore:
    def __init__(self, endpoint: str):
        self.container = ContainerClient(
            account_url=endpoint,
            container_name="invoice-jobs",
            credential=DefaultAzureCredential(),
        )

    def read(self, key: str) -> dict | None:
        try:
            return json.loads(self.container.download_blob(key).readall())
        except ResourceNotFoundError:
            return None

    def create(self, key: str, value: dict) -> bool:
        try:
            self.container.upload_blob(
                key,
                json.dumps(value, sort_keys=True).encode("utf-8"),
                overwrite=False,
            )
            return True
        except ResourceExistsError:
            # This is an atomic create-if-absent conflict, not a storage failure.
            return False


class SQLiteStore:
    """Local demo storage with the same atomic create-only contract as BlobStore."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS objects (key TEXT PRIMARY KEY, value TEXT)"
            )

    def read(self, key: str) -> dict | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT value FROM objects WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def create(self, key: str, value: dict) -> bool:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO objects (key, value) VALUES (?, ?)",
                (key, json.dumps(value, sort_keys=True)),
            )
        return cursor.rowcount == 1
