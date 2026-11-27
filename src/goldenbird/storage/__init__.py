"""Object storage adapters (R2 / S3-compatible)."""

from goldenbird.storage.r2 import ObjectStore, R2ObjectStore, create_r2_store

__all__ = ["ObjectStore", "R2ObjectStore", "create_r2_store"]
