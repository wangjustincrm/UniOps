#!/usr/bin/env python3
"""Generate a new UniOps module: a FastAPI service and a Vite/React front end.

    python3 tools/new-module/new_module.py \\
        --service orders-api --frontend order-bridge \\
        --module-key order_bridge --label "Order Bridge" \\
        --api-port 8014 --web-port 5182 --env-prefix ORDERS --table-prefix obr_ \\
        --perms order_bridge.read,order_bridge.config \\
        --platform inplace

It writes only the two new folders (refusing if either exists) and never edits
an existing file. The wiring a new module needs elsewhere in the repo
(compose, Caddy, env files, CORS, Portal, identity permissions, guide layer) is
printed as a checklist with snippets, to be applied by hand.

The template contains no platform code (design §13.16). --platform decides
what the skeleton expects:
  none     (default) no platform imports. The only value allowed for any
           module other than orders-api / order-bridge until the platform is
           extracted into shared packages (design §14.7).
  inplace  the skeleton imports app.platform / @/platform-ui, which are
           written into orders-api / order-bridge by hand. Refused for any
           other target. Nothing is ever copied.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
REPO = HERE.parent.parent

# The only targets that may be generated with --platform=inplace (design §13.16).
INPLACE_TARGETS = {("orders-api", "order-bridge")}

# vitest must support the template's vite major (8). Held at 4.0.x: every
# 4.1.x crashes npm 10 (the npm of node:20, used by the Dockerfile and the dev
# compose) with "Cannot read properties of null (reading 'edgesOut')" while it
# resolves peer sets — checked 2026-10-27 with 4.1.5 and 4.1.12.
VITEST_VERSION = "~4.0.18"

# Markers are %%name%% and %%#flag%% … %%/flag%% — not {{ }}, which is JSX.
# A block section has its markers on lines of their own and takes those lines
# with it; an inline section replaces only the text between its markers.
_BLOCK = re.compile(r"^%%([#^])(\w+)%%\n(.*?)^%%/\2%%\n", re.S | re.M)
_INLINE = re.compile(r"%%([#^])(\w+)%%(.*?)%%/\2%%", re.S)
_VAR = re.compile(r"%%(\w+)%%")


class TemplateError(Exception):
    pass


def render(text: str, flags: dict[str, bool], values: dict[str, str]) -> str:
    """%%#flag%%…%%/flag%% keeps the block when the flag is true,
    %%^flag%%…%%/flag%% when it is false; %%name%% is replaced by its value.
    A block whose markers stand on their own lines takes those lines with it."""

    def section(m: re.Match) -> str:
        kind, name, body = m.group(1), m.group(2), m.group(3)
        if name not in flags:
            raise TemplateError(f"unknown section flag {name!r}")
        return body if flags[name] == (kind == "#") else ""

    # Sections do not nest.
    text = _BLOCK.sub(section, text)
    text = _INLINE.sub(section, text)

    def var(m: re.Match) -> str:
        if m.group(1) not in values:
            raise TemplateError(f"unknown template variable {m.group(1)!r}")
        return values[m.group(1)]

    return _VAR.sub(var, text)


def _kebab(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", name):
        raise SystemExit(f"error: {name!r} is not a lower-case kebab-case folder name")
    return name


# The label lands in Python strings, TSX text and HTML <title>: plain words only.
LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 &-]{0,39}")


def build_values(a: argparse.Namespace) -> tuple[dict[str, bool], dict[str, str]]:
    if a.service == a.frontend:
        raise SystemExit(f"error: --service and --frontend are both {a.service!r}; they must be two folders")
    if not LABEL_RE.fullmatch(a.label) or a.label != a.label.strip() or "  " in a.label:
        raise SystemExit(
            f"error: --label {a.label!r} may use letters, digits, spaces, '&' and '-' only (≤ 40, no edge spaces)")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", a.module_key):
        raise SystemExit(f"error: --module-key {a.module_key!r} must be snake_case")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", a.env_prefix):
        raise SystemExit(f"error: --env-prefix {a.env_prefix!r} must be UPPER_SNAKE")
    if not re.fullmatch(r"[a-z]+_", a.table_prefix):
        raise SystemExit(f"error: --table-prefix {a.table_prefix!r} must look like 'abc_'")
    perms = [p.strip() for p in a.perms.split(",") if p.strip()]
    if not perms:
        raise SystemExit("error: --perms needs at least one permission key")
    for p in perms:
        if not p.startswith(a.module_key + "."):
            raise SystemExit(f"error: permission {p!r} must start with '{a.module_key}.'")
    short = a.env_prefix.lower()
    flags = {"platform": a.platform == "inplace"}
    values = {
        "service": a.service,
        "frontend": a.frontend,
        "module_key": a.module_key,
        "label": a.label,
        "service_title": f"UniOps {a.label} API",
        "api_port": str(a.api_port),
        "web_port": str(a.web_port),
        "env_prefix": a.env_prefix,
        "table_prefix": a.table_prefix,
        "version_table": f"alembic_version_{short}",
        "test_db": f"{short}_test",
        "auth_store": f"{a.frontend}-auth",
        "perm_list": ", ".join(f"'{p}'" for p in perms),
        "vitest_version": VITEST_VERSION,
    }
    return flags, values


def plan(repo: Path, flags: dict[str, bool], values: dict[str, str]) -> dict[Path, str]:
    """Every output file and its rendered content. Nothing is written here,
    so a template error leaves the repo untouched."""
    out: dict[Path, str] = {}
    for kind, target in (("service", values["service"]), ("frontend", values["frontend"])):
        root = TEMPLATES / kind
        for src in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = src.relative_to(root)
            raw = src.read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                out[repo / target / rel] = raw  # binary (none today), copied as is
                continue
            out[repo / target / rel] = render(text, flags, values)
    leftovers = [str(p) for p, c in out.items() if isinstance(c, str) and "%%" in c]
    if leftovers:
        raise TemplateError(f"unrendered markers in: {', '.join(leftovers)}")
    return out


def checklist(values: dict[str, str]) -> str:
    v = values
    up = v["env_prefix"]
    fe_var = v["frontend"].upper().replace("-", "_")
    return f"""
Wiring checklist for {v['label']} (design §2 / §14.2 — additions only, by hand)
================================================================================
[ ] docker-compose.dev.yml — API block (copy inventory-api's, then):
      {v['service']}:  build {v['service']}/Dockerfile, container ${{STACK_PREFIX:-uniops}}_{v['service'].replace('-', '_')},
      ports "${{{up}_API_PORT:-{v['api_port']}}}:{v['api_port']}", volumes ./{v['service']}:/app + ./packages/authz:/packages/authz,
      POSTGRES_* + JWT_* env, {up}_SCHEDULER_ENABLED: "false",
      command: uvicorn app.main:app --host 0.0.0.0 --port {v['api_port']} --reload
[ ] docker-compose.dev.yml — front-end block {v['frontend']}-frontend (node:20-alpine, port {v['web_port']},
      volumes ./{v['frontend']}:/app, {v['frontend'].replace('-', '_')}_node_modules:/app/node_modules,
      ./packages/shell:/packages/shell; VITE_API_URL / VITE_EPMS_API_URL / VITE_PORTAL_URL),
      the named volume, and VITE_{fe_var}_URL for portal-frontend
[ ] docker-compose.dev.yml — the {v['web_port']} origin in each of the 12 inline ALLOWED_ORIGINS lists
[ ] docker-compose.prod.yml — {v['service']}, {v['frontend']}-web, portal-web build arg VITE_{fe_var}_URL
[ ] Caddyfile — two sites:
      {v['frontend']}.canadaroyalmilk.com {{ import site_tls; reverse_proxy {v['frontend']}-web:80 }}
      {v['service']}.canadaroyalmilk.com {{ import site_tls; reverse_proxy {v['service']}:{v['api_port']} }}
[ ] .env.prod.example, .env.lan.example, epms-api/.env.example — URLs and the origin
[ ] make-stack-env.sh ({up}_API_PORT={v['api_port']}+off, {fe_var}_PORT={v['web_port']}+off), migrate-prod.sh
      ({v['service']} after identity-api; version table {v['version_table']}), check-health.sh, reset-db.sh,
      run_tests.sh, DEPLOYMENT.md — one entry each
[ ] */app/core/config.py of every existing service — the origin in the ALLOWED_ORIGINS default
[ ] portal: navConfig.tsx MODULES entry (anyPermission: the module keys), resolveNavHref,
      lib/api.ts {fe_var}_URL + RETURN_URL_ORIGINS, PortalHome.tsx card, AdminPanel.tsx
      MODULE_TAGLINE_KEYS {{key:'{v['module_key']}', label:'{v['label']}'}}
[ ] identity-api migration: permission keys [{v['perm_list']}] (module '{v['module_key']}'),
      granted to system_admin — check the single head and the ≤ 32-character revision id first
[ ] task types (if any): epms/src/lib/taskTypes.ts, epms/src/services/tasks.ts, Portal labels,
      epms-api/app/crud/task.py _TASK_NO_STATUS_INVARIANT
[ ] guide layer: epms-api/app/knowledge/modules.yaml entry '{v['module_key']}' + tests/test_module_guide.py
[ ] {v['frontend']}: run `npm install --install-links` once (beside a copy of packages/shell, as the
      Dockerfile does) to create {v['frontend']}/package-lock.json; never touch the root lock file
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--service", required=True, type=_kebab, help="back-end folder, e.g. orders-api")
    ap.add_argument("--frontend", required=True, type=_kebab, help="front-end folder, e.g. order-bridge")
    ap.add_argument("--module-key", required=True, help="permission/branding module key, e.g. order_bridge")
    ap.add_argument("--label", required=True, help='display name, e.g. "Order Bridge"')
    ap.add_argument("--api-port", required=True, type=int)
    ap.add_argument("--web-port", required=True, type=int)
    ap.add_argument("--env-prefix", required=True, help="settings prefix, e.g. ORDERS")
    ap.add_argument("--table-prefix", required=True, help="table prefix, e.g. obr_")
    ap.add_argument("--perms", required=True, help="comma-separated permission keys; the first gates Home")
    ap.add_argument("--platform", choices=("none", "inplace"), default="none")
    ap.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.platform == "inplace" and (a.service, a.frontend) not in INPLACE_TARGETS:
        print(
            f"error: --platform=inplace is only for orders-api / order-bridge (got {a.service} / {a.frontend}). "
            "The platform is used by Order Bridge only until the extraction decision (design §14.7); "
            "use --platform=none.",
            file=sys.stderr,
        )
        return 2

    flags, values = build_values(a)
    repo: Path = a.repo.resolve()
    for folder in (a.service, a.frontend):
        if (repo / folder).exists():
            print(f"error: {repo / folder} already exists; the template never overwrites", file=sys.stderr)
            return 2

    files = plan(repo, flags, values)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    print(f"created {a.service}/ and {a.frontend}/ ({len(files)} files, platform={a.platform})")
    print(checklist(values))
    return 0


if __name__ == "__main__":
    sys.exit(main())
