"""In-memory gateways for tests and for every non-production stack. They
record each call so tests can assert on what would have gone out."""
from __future__ import annotations

import posixpath
from collections.abc import Callable
from typing import Any

from app.platform.gateways.base import ExternalEmail, RemoteFile


class FakeSftp:
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


class FakeEmail:
    """`script` (optional) is called with the attempt number and may raise
    NotAccepted / AcceptedThenFailed to simulate the server's answer."""

    def __init__(self, script: Callable[[int], None] | None = None) -> None:
        self.sent: list[ExternalEmail] = []
        self.attempts = 0
        self.script = script

    async def send_external(self, msg: ExternalEmail) -> None:
        self.attempts += 1
        if self.script is not None:
            self.script(self.attempts)
        self.sent.append(msg)
