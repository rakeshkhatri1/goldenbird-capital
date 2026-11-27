from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from goldenbird.storage.r2 import R2ObjectStore, StorageError, _guess_content_type


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "HeadObject")


def test_put_and_get_bytes_round_trip() -> None:
    client = MagicMock()
    body = MagicMock()
    body.read.return_value = b'{"ok": true}'
    client.get_object.return_value = {"Body": body}
    store = R2ObjectStore(client, "bucket")

    key = store.put_bytes("ui/demo.json", b'{"ok": true}', content_type="application/json")
    assert key == "ui/demo.json"
    client.put_object.assert_called_once()
    assert store.get_bytes("ui/demo.json") == b'{"ok": true}'


def test_exists_handles_missing() -> None:
    client = MagicMock()
    client.head_object.side_effect = _client_error("404")
    store = R2ObjectStore(client, "bucket")
    assert store.exists("ui/missing.json") is False


def test_exists_reraises_unexpected_errors() -> None:
    client = MagicMock()
    client.head_object.side_effect = _client_error("500")
    store = R2ObjectStore(client, "bucket")
    with pytest.raises(StorageError):
        store.exists("ui/x.json")


def test_upload_file_guesses_json_content_type(tmp_path: Path) -> None:
    path = tmp_path / "peer.json"
    path.write_text("{}", encoding="utf-8")
    client = MagicMock()
    store = R2ObjectStore(client, "bucket")
    store.upload_file(path, "ui/peer.json")
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["ContentType"].startswith("application/json")
    assert kwargs["Key"] == "ui/peer.json"


def test_guess_content_type() -> None:
    assert _guess_content_type(Path("a.json")).startswith("application/json")
    assert _guess_content_type(Path("a.txt")).startswith("text/plain")
    assert _guess_content_type(Path("a.bin")) == "application/octet-stream"
