"""Cloudflare R2 (S3-compatible) object store.

Design notes
------------
* UI packs live under ``ui/`` (safe to expose publicly later).
* Raw filing text / manifests live under ``sources/`` (keep private).
* Market candles live under ``market/``.
* Callers depend on :class:`ObjectStore` so tests can inject fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from goldenbird.config import Settings, get_settings

UI_PREFIX = "ui/"
SOURCES_PREFIX = "sources/"
MARKET_PREFIX = "market/"


class StorageError(RuntimeError):
    """Raised when an object-store operation fails."""


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal storage contract used by builders and CLIs."""

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        ...

    def get_bytes(self, key: str) -> bytes:
        ...

    def exists(self, key: str) -> bool:
        ...

    def delete(self, key: str) -> None:
        ...

    def upload_file(
        self,
        local_path: Path | str,
        key: str,
        *,
        content_type: str | None = None,
    ) -> str:
        ...


def _guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".json": "application/json; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".pdf": "application/pdf",
    }.get(suffix, "application/octet-stream")


class R2ObjectStore:
    """Thin wrapper over boto3 S3 client pointed at Cloudflare R2."""

    def __init__(self, client: BaseClient, bucket: str) -> None:
        if not bucket:
            raise ValueError("bucket is required")
        self._client = client
        self._bucket = bucket

    @property
    def bucket(self) -> str:
        return self._bucket

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> str:
        key = key.lstrip("/")
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
        except ClientError as exc:
            raise StorageError(f"put_object failed for s3://{self._bucket}/{key}: {exc}") from exc
        return key

    def get_bytes(self, key: str) -> bytes:
        key = key.lstrip("/")
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            raise StorageError(f"get_object failed for s3://{self._bucket}/{key}: {exc}") from exc

    def exists(self, key: str) -> bool:
        key = key.lstrip("/")
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise StorageError(f"head_object failed for s3://{self._bucket}/{key}: {exc}") from exc

    def delete(self, key: str) -> None:
        key = key.lstrip("/")
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise StorageError(
                f"delete_object failed for s3://{self._bucket}/{key}: {exc}"
            ) from exc

    def upload_file(
        self,
        local_path: Path | str,
        key: str,
        *,
        content_type: str | None = None,
    ) -> str:
        path = Path(local_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        data = path.read_bytes()
        return self.put_bytes(
            key,
            data,
            content_type=content_type or _guess_content_type(path),
        )

    def list_keys(self, prefix: str = "", *, max_keys: int = 1000) -> list[str]:
        prefix = prefix.lstrip("/")
        try:
            response = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
        except ClientError as exc:
            raise StorageError(f"list_objects_v2 failed for prefix={prefix!r}: {exc}") from exc
        return [item["Key"] for item in response.get("Contents", [])]


def create_r2_store(settings: Settings | None = None) -> R2ObjectStore:
    """Build an :class:`R2ObjectStore` from settings / environment."""
    cfg = settings or get_settings()
    if not cfg.r2_configured:
        raise StorageError(
            "R2 is not configured. Set R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, "
            "R2_BUCKET, and R2_ENDPOINT (or R2_ACCOUNT_ID) in .env"
        )
    client = boto3.client(
        "s3",
        endpoint_url=cfg.r2_endpoint,
        aws_access_key_id=cfg.r2_access_key_id,
        aws_secret_access_key=cfg.r2_secret_access_key,
        region_name="auto",
    )
    return R2ObjectStore(client, cfg.r2_bucket)
