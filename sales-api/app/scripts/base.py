"""What every one-off script shares (2026-11-10): a dry run unless `--apply`,
no SQL echoed (statements carry values), the result as one JSON line, and on a
failure only the error's type (its text could carry a customer's or a card's
data) with exit code 1.

    async def run(args: ScriptArgs) -> dict: ...
    if __name__ == "__main__":
        script_main(run)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScriptArgs:
    argv: tuple[str, ...]
    # The flags followed by a value (`--order 1009000`); every other `--x` stands alone.
    takes_values: tuple[str, ...] = ()

    @property
    def apply(self) -> bool:
        """Without it nothing is kept: the script reports what it would do."""
        return "--apply" in self.argv

    def flag(self, name: str) -> bool:
        return f"--{name}" in self.argv

    def value(self, name: str) -> str | None:
        """The text after `--name` (one of takes_values), or None."""
        a = self.argv
        for i, x in enumerate(a[:-1]):
            if x == f"--{name}":
                return a[i + 1]
        return None

    @property
    def positional(self) -> list[str]:
        """The arguments that are neither a `--flag` nor a flag's value."""
        out, i, a = [], 0, self.argv
        while i < len(a):
            if a[i].startswith("--"):
                i += 2 if a[i][2:] in self.takes_values else 1
            else:
                out.append(a[i])
                i += 1
        return out


def quiet_sql() -> None:
    """The engine echoes statements when DEBUG is on, parameters included: never here."""
    from app.db.session import engine
    engine.sync_engine.echo = False
    engine.sync_engine.hide_parameters = True  # nor in an error's text
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def script_main(run: Callable[[ScriptArgs], Awaitable[dict[str, Any]]], *, takes_values: tuple[str, ...] = (),
                argv: list[str] | None = None) -> int:
    """Runs `run` and prints {"apply": …, **its result}. Returns the exit code (also raised as SystemExit
    when called as the script's entry)."""
    args = ScriptArgs(tuple(sys.argv[1:] if argv is None else argv), takes_values)
    quiet_sql()
    try:
        out = asyncio.run(run(args))
    except Exception as e:  # noqa: BLE001 — its text could carry a value
        print(json.dumps({"apply": args.apply, "error": type(e).__name__}))
        code = 1
    else:
        print(json.dumps({"apply": args.apply, **out}, default=str))
        code = 0
    if argv is None:
        raise SystemExit(code)
    return code
