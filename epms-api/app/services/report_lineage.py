"""Where the numbers on a report come from.

The question this answers, in the words it was asked in: "在这个 Monthly Plan
vs Actual 的表里你是怎么获取 Actual 数据,并按照什么原则把它分配到对应的成本中心
的" — and, the same afternoon, "SELL-0107-S03 对应 NC JV 里是哪个成本中心或部门".
Managers and finance ask this every time a cell looks wrong, and the assistant
had nowhere to send it: it answered that the data flow was internal and they
should ask Finance, while every part of the answer existed in code or in a
twenty-row table.

It is the same two-layer shape as the document-kind guide, with one addition.

  prose    knowledge/report_budget_plan_vs_actual.yaml — why there are three
           figures, which to trust, what to check when one is wrong. A finance
           decision; no code states it.

  derived  the rules themselves, from the services that own them.
           finance-api  → the NC figure: categories, exclusions, posted-only,
                          and the cost-centre resolver's own precedence.
           budget-api   → plan and actual (docs): which plans count, which
                          ledger operations are actual, and what the ledger
                          currently holds.

  live     the mapping rules, read from budget_actual_cc_map through the same
           ontology declaration the query layer uses. Finance edits those rows
           without a deploy, so anything written down here would be wrong by
           the time it mattered.

Unreachable services are reported as unavailable, never filled in from memory:
the whole value of this route is that it says what the system does rather than
what someone once wrote down about it, and a plausible answer assembled from a
stale copy would be worse than admitting the gap.
"""
from __future__ import annotations

import functools
from pathlib import Path

import sqlalchemy as sa
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ontology import get_entity, may_view
from app.services import budget_client, finance_client

_KNOWLEDGE = Path(__file__).resolve().parent.parent / "knowledge"

# report key -> prose file. One today; the shape is here because "where does
# this number come from" is not a question about one screen.
_REPORTS = {"budget_plan_vs_actual": "report_budget_plan_vs_actual.yaml"}
SUPPORTED = tuple(_REPORTS)

# The ontology entity the mapping rules live behind. Named rather than
# hardcoded as a table so the guide and the query layer cannot disagree about
# what the rules are or about who may see them.
_MAP_ENTITY = "nc_cost_center_map"


@functools.lru_cache(maxsize=None)
def _load(report: str) -> dict:
    name = _REPORTS.get(report)
    if name is None:
        raise LookupError(report)
    with (_KNOWLEDGE / name).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


async def _mapping_rules(db: AsyncSession, perms: dict) -> dict:
    """The live map, if this caller is allowed to see it.

    Gated by the ontology entity's own keys rather than by a second opinion
    about who may look at it. Someone without them still gets the whole
    explanation of how the mapping works — what they do not get is the list of
    which department lands where, and they are told that is why.
    """
    entity = get_entity(_MAP_ENTITY)
    if entity is None:          # pragma: no cover - the ontology test catches this
        return {"available": False, "why": "The mapping table is not declared."}
    if not may_view(entity, perms):
        return {
            "available": False,
            "why": ("Listing the rules needs budget or finance access. How the "
                    "mapping works is not restricted; which cost centre each "
                    "department maps to is."),
        }
    m = entity.model
    rows = (await db.execute(
        sa.select(m.account_code, m.dept_code, m.nc_cc_code, m.uniops_cc_code)
        .order_by(m.account_code, m.dept_code, m.nc_cc_code))).all()
    return {
        "available": True,
        "count": len(rows),
        "wildcard": "ALL means 'any', not a code — a rule with ALL applies to "
                    "every department or every cost-centre code under it.",
        "rules": [{"expense_account": a, "nc_department": d,
                   "nc_cost_centre": c, "uniops_cost_centre": u}
                  for a, d, c, u in rows],
    }


async def build(db: AsyncSession, report: str, perms: dict,
                token: str | None) -> dict:
    """The prose, the rules as the owning services state them, and the live map."""
    doc = _load(report)
    nc = await finance_client.budget_actual_lineage(bearer_token=token)
    docs = await budget_client.get_actuals_lineage(token)

    figures: list[dict] = []
    unavailable: list[str] = []
    if docs and docs.get("figures"):
        figures.extend(docs["figures"])
    else:
        unavailable.append("plan and actual (docs), owned by budget-api")
    if nc:
        figures.append(nc)
    else:
        unavailable.append("NC posted, owned by finance-api")

    return {
        "report": report,
        "label": doc.get("label", report),
        "where_it_lives": (doc.get("where_it_lives") or "").strip(),
        "what_it_is": (doc.get("what_it_is") or "").strip(),
        "why_three_figures": (doc.get("why_three_figures") or "").strip(),
        "which_one_is_the_truth": (doc.get("which_one_is_the_truth") or "").strip(),
        # Derived: each figure as the service that computes it describes it.
        "figures": figures,
        # Live: the rules finance maintains, not a copy of them.
        "cost_centre_mapping": await _mapping_rules(db, perms),
        "notes": doc.get("notes") or [],
        "when_a_cell_looks_wrong": (doc.get("when_a_cell_looks_wrong") or "").strip(),
        "who_maintains_it": (doc.get("who_maintains_it") or "").strip(),
        # Said out loud rather than left for the narrator to notice, so a
        # missing half is reported as missing instead of quietly not mentioned.
        "unavailable": unavailable,
        "sources_are": (
            "Every rule under `figures` is reported by the service that "
            "computes that figure, and `cost_centre_mapping.rules` is the live "
            "table. Anything listed in `unavailable` could not be reached — say "
            "so rather than describing that part."
        ),
    }
