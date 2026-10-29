"""SFTP integration (design §5.1 / §13.9): the gateway protocol, an in-memory
fake for tests and non-production stacks, and what the outbound guard needs
to know about it — which methods are writes (and their switch), which are
reads, the LIVE-only rule and the "would have written" summary — and the real
asyncssh gateway (P2).

The real gateway refuses before any network traffic unless the host is in the
stack's allow-list (SALES_SFTP_ALLOWED_HOSTS, fail closed), the connection's
host key is pinned (no trust on first use, §5.1) and its secret is present in
the environment (§5.3). A refused login is `SftpAuthFailed`, which latches
the connection (FR-OB-50); everything else is an ordinary failure.
"""
from __future__ import annotations

import hashlib
import posixpath
import stat
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass(frozen=True)
class RemoteFile:
    path: str
    name: str
    size: int
    modified_at: datetime | None = None


@dataclass(frozen=True)
class HostKey:
    """A server's public host key as pinned (`line`) and as shown (`fingerprint`)."""
    line: str
    fingerprint: str


@dataclass(frozen=True)
class FolderCheck:
    folder: str
    ok: bool
    files: int | None = None
    error: str | None = None


class SftpError(Exception):
    """An SFTP call that did not happen or did not succeed."""


class SftpAuthFailed(SftpError):
    """The server refused the login. Latches the connection (FR-OB-50)."""


class SftpRefused(SftpError):
    """Refused before connecting: host not allowed, no pinned key, no secret."""


class SftpHostKeyMismatch(SftpError):
    """The server presented a host key other than the pinned one."""


def readable(e: BaseException) -> str:
    """A short message for a person (the connection test, the log line of a
    failed listing). Never the library's raw text: it can be long, cryptic or
    carry what the server answered."""
    import asyncio
    import socket

    import asyncssh

    if isinstance(e, SftpRefused):
        return str(e)  # our own text: not allowed / not pinned / no secret
    if isinstance(e, SftpAuthFailed):
        return "The server refused the login: check the user name and the password in the server environment."
    if isinstance(e, SftpHostKeyMismatch):
        return ("The server presented a different host key than the pinned one. Do not pin the new key unless "
                "the partner confirms it changed.")
    if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
        return "The server did not answer in time."
    if isinstance(e, ConnectionRefusedError):
        return "The server refused the connection on that port."
    if isinstance(e, socket.gaierror):
        return "The host name could not be found."
    if isinstance(e, asyncssh.SFTPNoSuchFile):
        return "Folder not found."
    if isinstance(e, asyncssh.SFTPPermissionDenied):
        return "Permission denied on that folder."
    if isinstance(e, (asyncssh.Error, ConnectionResetError, EOFError)):
        return "The server did not answer as an SFTP server on that port."
    return f"The connection failed ({type(e).__name__})."


class SftpGateway(Protocol):
    # `conn` is whatever connection record the business code keeps (host,
    # credentials, LIVE/TEST mode); the gateway only reads what it needs.
    async def list(self, conn: Any, folder: str) -> list[RemoteFile]: ...
    async def get(self, conn: Any, path: str) -> bytes: ...
    async def put(self, conn: Any, folder: str, name: str, data: bytes) -> None: ...
    async def move(self, conn: Any, src: str, dst: str) -> None: ...
    async def delete(self, conn: Any, path: str) -> None: ...
    # The connection test (FR-OB-74): reads only.
    async def host_key(self, conn: Any) -> HostKey: ...
    async def check(self, conn: Any, folders: list[str]) -> list[FolderCheck]: ...


WRITES = ("put", "move", "delete")
READS = ("list", "get", "host_key", "check")


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
        # Remote modified times: per path, else one for all (old enough to be read).
        self.mtimes: dict[str, datetime] = {}
        self.default_mtime = datetime(2026, 1, 1, tzinfo=timezone.utc)
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
            RemoteFile(path=p, name=posixpath.basename(p), size=len(b), modified_at=self.mtimes.get(p, self.default_mtime))
            for p, b in sorted(self.files.items())
            if p.startswith(prefix) and "/" not in p[len(prefix):]
        ]

    async def host_key(self, conn: Any) -> HostKey:
        self._call("host_key")
        return HostKey("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOYyzHbjnpUgsd3f+LSfKEJkQUehyBzwkRpZ0hBihPDh", "SHA256:fake")

    async def check(self, conn: Any, folders: list[str]) -> list[FolderCheck]:
        self._call("check", tuple(folders))
        return [FolderCheck(f, True, files=len([p for p in self.files if p.startswith(f.rstrip("/") + "/")])) for f in folders]

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
        if src in self.mtimes:
            self.mtimes[dst] = self.mtimes.pop(src)

    async def delete(self, conn: Any, path: str) -> None:
        self._call("delete", path)
        self.files.pop(path, None)


class AsyncsshSftp:
    """The real gateway. One SSH connection per call: a pass makes a handful
    of calls per connection, and nothing is held open between cycles."""

    def __init__(self, *, allowed_hosts: frozenset[str], secret: Callable[[str], str | None],
                 connect_timeout: float = 20.0) -> None:
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)
        self.secret = secret
        self.connect_timeout = connect_timeout

    def _allowed(self, conn: Any) -> str:
        host = (getattr(conn, "host", None) or "").strip()
        if not host or host.lower() not in self.allowed_hosts:
            raise SftpRefused(f"host {host!r} is not in SALES_SFTP_ALLOWED_HOSTS")
        return host

    @asynccontextmanager
    async def _client(self, conn: Any) -> AsyncIterator[Any]:
        import asyncssh

        host = self._allowed(conn)
        if not getattr(conn, "host_key", None):
            raise SftpRefused("the connection's host key is not pinned; run the connection test and pin it")
        name = getattr(conn, "secret_name", None)
        password = self.secret(name) if name else None
        if not password:
            raise SftpRefused(f"secret {name!r} is not set on the server")
        try:
            key = asyncssh.import_public_key(conn.host_key)
        except (asyncssh.KeyImportError, ValueError) as e:
            raise SftpRefused(f"the pinned host key cannot be read: {e}") from e
        try:
            async with asyncssh.connect(
                host, port=getattr(conn, "port", None) or 22, username=conn.username, password=password,
                known_hosts=([key], [], []), client_keys=None, agent_path=None,
                connect_timeout=self.connect_timeout,
            ) as ssh, ssh.start_sftp_client() as sftp:
                yield sftp
        except asyncssh.PermissionDenied as e:
            raise SftpAuthFailed(f"login refused by {host}: {e.reason}") from e
        except asyncssh.HostKeyNotVerifiable as e:
            raise SftpHostKeyMismatch(f"{host} presented a host key other than the pinned one") from e

    async def list(self, conn: Any, folder: str) -> list[RemoteFile]:
        async with self._client(conn) as sftp:
            out = []
            for entry in await sftp.readdir(folder):
                a = entry.attrs
                if entry.filename in (".", "..") or a.permissions is None or not stat.S_ISREG(a.permissions):
                    continue
                out.append(RemoteFile(
                    path=posixpath.join(folder, entry.filename), name=entry.filename, size=int(a.size or 0),
                    modified_at=datetime.fromtimestamp(a.mtime, timezone.utc) if a.mtime is not None else None,
                ))
            return sorted(out, key=lambda f: (f.modified_at or datetime.min.replace(tzinfo=timezone.utc), f.name))

    async def get(self, conn: Any, path: str) -> bytes:
        async with self._client(conn) as sftp, sftp.open(path, "rb") as f:
            return await f.read()

    async def put(self, conn: Any, folder: str, name: str, data: bytes) -> None:
        async with self._client(conn) as sftp, sftp.open(posixpath.join(folder, name), "wb") as f:
            await f.write(data)

    async def move(self, conn: Any, src: str, dst: str) -> None:
        async with self._client(conn) as sftp:
            await sftp.rename(src, dst)

    async def delete(self, conn: Any, path: str) -> None:
        async with self._client(conn) as sftp:
            await sftp.remove(path)

    async def host_key(self, conn: Any) -> HostKey:
        """The key the server presents — read without logging in, so an
        admin can compare its fingerprint and pin it."""
        import asyncssh

        host = self._allowed(conn)
        key = await asyncssh.get_server_host_key(host, getattr(conn, "port", None) or 22)
        if key is None:
            raise SftpError(f"{host} presented no host key")
        return HostKey(key.export_public_key("openssh").decode().strip(), key.get_fingerprint("sha256"))

    async def check(self, conn: Any, folders: list[str]) -> list[FolderCheck]:
        """Lists each folder; creates, moves and deletes nothing (FR-OB-74)."""
        out = []
        async with self._client(conn) as sftp:
            for folder in folders:
                try:
                    out.append(FolderCheck(folder, True, files=len([n for n in await sftp.listdir(folder) if n not in (".", "..")])))
                except Exception as e:  # noqa: BLE001 — one missing folder is a result, not a failure
                    out.append(FolderCheck(folder, False, error=readable(e)))
        return out
