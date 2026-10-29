"""SFTP integration (design §5.1 / §13.9): the gateway protocol, an in-memory
fake for tests and non-production stacks, and what the outbound guard needs
to know about it — which methods are writes (and their switch), which are
reads, the LIVE-only rule and the "would have written" summary.

The real asyncssh gateway arrives with P2.
"""
from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RemoteFile:
    path: str
    name: str
    size: int
    modified_at: datetime | None = None


class SftpGateway(Protocol):
    # `conn` is whatever connection record the business code keeps (host,
    # credentials, LIVE/TEST mode); the gateway only reads what it needs.
    async def list(self, conn: Any, folder: str) -> list[RemoteFile]: ...
    async def get(self, conn: Any, path: str) -> bytes: ...
    async def put(self, conn: Any, folder: str, name: str, data: bytes) -> None: ...
    async def move(self, conn: Any, src: str, dst: str) -> None: ...
    async def delete(self, conn: Any, path: str) -> None: ...


WRITES = ("put", "move", "delete")
READS = ("list", "get")


def guarded(switch: str) -> dict[str, str]:
    """The guard's write map: every SFTP write behind `switch`."""
    return {m: switch for m in WRITES}


def live_only(method: str, args: tuple, kwargs: dict) -> str | None:  # noqa: ARG001
    """SFTP writes also need the connection in LIVE mode (design §5.4)."""
    conn = args[0] if args else kwargs.get("conn")
    return None if getattr(conn, "mode", None) == "LIVE" else "connection not LIVE"


def summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    """The "would have written" record: never the file's bytes."""
    names = {"put": ("conn", "folder", "name", "data"), "move": ("conn", "src", "dst"), "delete": ("conn", "path")}
    a = dict(zip(names[method], args)) | kwargs
    conn = a.pop("conn", None)
    out: dict[str, Any] = {"connection": getattr(conn, "id", None), "mode": getattr(conn, "mode", None)}
    data = a.pop("data", None)
    if data is not None:
        out |= {"size": len(data), "sha256": hashlib.sha256(bytes(data)).hexdigest()}
    return out | {k: str(v) for k, v in a.items()}


class FakeSftp:
    """Records each call so tests can assert on what would have gone out."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})  # full path → bytes
        self.calls: list[tuple[str, tuple]] = []
        # Optional failure injection: method name → exception to raise.
        self.fail: dict[str, BaseException] = {}

    def _call(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if name in self.fail:
            raise self.fail[name]

    async def list(self, conn: Any, folder: str) -> list[RemoteFile]:
        self._call("list", folder)
        prefix = folder.rstrip("/") + "/"
        return [
            RemoteFile(path=p, name=posixpath.basename(p), size=len(b))
            for p, b in sorted(self.files.items())
            if p.startswith(prefix) and "/" not in p[len(prefix):]
        ]

    async def get(self, conn: Any, path: str) -> bytes:
        self._call("get", path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def put(self, conn: Any, folder: str, name: str, data: bytes) -> None:
        self._call("put", folder, name, len(data))
        self.files[posixpath.join(folder, name)] = data

    async def move(self, conn: Any, src: str, dst: str) -> None:
        self._call("move", src, dst)
        self.files[dst] = self.files.pop(src)

    async def delete(self, conn: Any, path: str) -> None:
        self._call("delete", path)
        self.files.pop(path, None)
