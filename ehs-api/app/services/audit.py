"""The evidence pack an auditor asks for, assembled from what is already there.

COR and ISO 45001 audits, and a Ministry inspection, all ask variations of the
same short list: show me your incidents and what you did about them, your
corrective actions and whether they closed on time, your training records, your
committee minutes, your inspections, your data sheets. None of that is new
information — it is the module, counted.

Two principles run through every figure here.

**A rate over nothing is not a rate.** Zero of zero corrective actions closed on
time is not 100% compliance, and presenting it as such is the single most
common way a compliance dashboard lies. Every percentage in this pack is
returned as null when its denominator is zero, and the page says "none in this
period" rather than showing a green figure.

**Missed is counted separately from met.** An auditor is not interested in how
many deadlines existed; they want the ones that were missed, by name, with
their dates. So the statutory section returns the breaches themselves rather
than a percentage that hides them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _rate(numerator: int, denominator: int) -> float | None:
    """A percentage, or None when there is nothing to compute it over.

    Zero of zero is not one hundred per cent. Returning None forces the caller
    to say "none in this period", which is the truth.
    """
    if denominator <= 0:
        return None
    return round(numerator * 100 / denominator, 1)


@dataclass
class Period:
    date_from: date
    date_to: date

    @property
    def params(self) -> dict:
        return {"start": self.date_from, "end": self.date_to}


async def _one(db: AsyncSession, sql: str, params: dict) -> tuple:
    return (await db.execute(text(sql), params)).one()


async def incidents(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          count(*)                                                    as total,
          count(*) filter (where injury_class is not null)            as injuries,
          count(*) filter (where injury_class = 'lost_time')          as lost_time,
          count(*) filter (where mol_reportable)                      as mol_reportable,
          count(*) filter (where form_kind = 'near_miss')             as near_misses,
          count(*) filter (where status = 'closed')                   as closed,
          count(*) filter (where hse_reviewed_at is not null)         as hse_reviewed,
          count(*) filter (where injury_class is not null
                             and id in (select incident_id from ehs_incident_investigations))
                                                                      as investigated_injuries
        from ehs_incidents
       where occurred_at >= :start and occurred_at < :end
    """, period.params)
    total, injuries, lost_time, mol, near_miss, closed, reviewed, investigated = row
    return {
        "total": total, "injuries": injuries, "lost_time": lost_time,
        "mol_reportable": mol, "near_misses": near_miss, "closed": closed,
        "hse_reviewed": reviewed,
        "investigated_injuries": investigated,
        # HSE made investigation mandatory for every injury. This is the number
        # that says whether that actually happens.
        "injury_investigation_rate": _rate(investigated, injuries),
        "closure_rate": _rate(closed, total),
    }


async def statutory(db: AsyncSession, period: Period) -> dict:
    """Legal clocks: how many ran, and which were missed.

    The breaches come back by name. An auditor does not want a percentage that
    hides them; they want the list, with dates, so they can ask about each one.
    """
    rows = (await db.execute(text("""
        select kind, regulation_ref, source_ref,
               due_at, satisfied_at,
               (satisfied_at is not null and satisfied_at <= due_at) as met
          from ehs_statutory_deadlines
         where starts_at >= :start and starts_at < :end
         order by due_at
    """), period.params)).all()

    now = datetime.now(timezone.utc)
    met, missed, open_now = 0, [], []
    for kind, ref, source, due_at, satisfied_at, was_met in rows:
        if was_met:
            met += 1
        elif satisfied_at is not None:
            missed.append({
                "kind": kind, "regulation": ref, "reference": source,
                "due_at": due_at.isoformat(), "satisfied_at": satisfied_at.isoformat(),
                "late_by_hours": round((satisfied_at - due_at).total_seconds() / 3600, 1),
            })
        elif due_at < now:
            missed.append({
                "kind": kind, "regulation": ref, "reference": source,
                "due_at": due_at.isoformat(), "satisfied_at": None,
                "late_by_hours": round((now - due_at).total_seconds() / 3600, 1),
            })
        else:
            open_now.append({"kind": kind, "reference": source, "due_at": due_at.isoformat()})

    total = met + len(missed)
    return {
        "total": len(rows), "met": met, "missed": missed, "still_open": open_now,
        "on_time_rate": _rate(met, total),
    }


async def actions(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          count(*)                                            as raised,
          count(*) filter (where status = 'closed')           as closed,
          count(*) filter (where status = 'closed'
                             and closed_at::date <= due_date) as closed_on_time,
          count(*) filter (where status <> 'closed'
                             and due_date < current_date)     as overdue_now,
          count(*) filter (where verified_at is not null)     as verified
        from ehs_actions
       where created_at >= :start and created_at < :end
    """, period.params)
    raised, closed, on_time, overdue, verified = row
    return {
        "raised": raised, "closed": closed, "closed_on_time": on_time,
        "overdue_now": overdue, "verified": verified,
        "on_time_rate": _rate(on_time, closed),
        "closure_rate": _rate(closed, raised),
        # A corrective action nobody checked afterwards is a corrective action
        # that may not have worked.
        "verification_rate": _rate(verified, closed),
    }


async def training(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          (select count(*) from ehs_training_records
            where completed_on >= :start and completed_on < :end)          as records,
          (select count(*) from ehs_course_deliveries
            where session_date >= :start and session_date < :end)          as sessions,
          (select count(*) from ehs_courses where is_statutory)            as statutory_courses,
          (select count(*) from users where is_active)                     as workforce,
          (select count(*) from ehs_worker_certifications
            where expires_on is not null and expires_on < current_date)    as expired_certs
    """, period.params)
    records, sessions, statutory_courses, workforce, expired = row

    from app.crud.training import training_gaps

    gaps = await training_gaps(db)
    return {
        "records_added": records, "sessions_held": sessions,
        "statutory_courses": statutory_courses, "workforce": workforce,
        "expired_certifications": expired,
        "workers_compliant": gaps.workers_compliant,
        "workers_considered": gaps.workers_considered,
        "compliance_rate": _rate(gaps.workers_compliant, gaps.workers_considered),
        "outstanding_gaps": len(gaps.gaps),
    }


async def committee(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          (select count(*) from ehs_jhsc_meetings
            where held_on >= :start and held_on < :end)                     as meetings,
          (select count(*) from ehs_jhsc_meetings
            where held_on >= :start and held_on < :end
              and status = 'published')                                     as published,
          (select count(*) from ehs_jhsc_members where is_active)           as members,
          (select count(*) from ehs_jhsc_members
            where is_active and representation = 'worker')                  as worker_members,
          (select count(*) from ehs_jhsc_members
            where is_active and is_certified)                               as certified,
          (select count(*) from ehs_jhsc_recommendations r
             join ehs_jhsc_meetings m on m.id = r.meeting_id
            where m.held_on >= :start and m.held_on < :end)                 as recommendations,
          (select count(*) from ehs_jhsc_recommendations r
             join ehs_jhsc_meetings m on m.id = r.meeting_id
            where m.held_on >= :start and m.held_on < :end
              and r.responded_at is not null)                               as answered
    """, period.params)
    meetings, published, members, workers, certified, recs, answered = row
    return {
        "meetings": meetings, "minutes_published": published,
        "members": members, "worker_members": workers, "certified_members": certified,
        # OHSA s.9(6): at least half must be workers who do not exercise
        # managerial functions. Stated as met or not, because that is how it
        # is asked.
        "worker_majority_met": members > 0 and workers * 2 >= members,
        "recommendations": recs, "answered": answered,
        "reply_rate": _rate(answered, recs),
    }


async def inspections(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          (select count(*) from ehs_form_schedule_runs
            where due_on >= :start and due_on < :end)                       as due,
          (select count(*) from ehs_form_schedule_runs
            where due_on >= :start and due_on < :end and status='completed') as completed,
          (select count(*) from ehs_form_schedule_runs
            where due_on >= :start and due_on < :end and status='missed')    as missed,
          (select count(*) from ehs_asset_inspections
            where due_on >= :start and due_on < :end)                        as asset_due,
          (select count(*) from ehs_asset_inspections
            where due_on >= :start and due_on < :end
              and result in ('passed','failed'))                             as asset_done,
          (select count(*) from ehs_safety_assets
            where status = 'out_of_service')                                 as out_of_service
    """, period.params)
    due, completed, missed, asset_due, asset_done, out_of_service = row
    return {
        "scheduled": due, "completed": completed, "missed": missed,
        "completion_rate": _rate(completed, due),
        "equipment_checks_due": asset_due, "equipment_checks_done": asset_done,
        "equipment_check_rate": _rate(asset_done, asset_due),
        "equipment_out_of_service": out_of_service,
    }


async def documents(db: AsyncSession, period: Period) -> dict:  # noqa: ARG001
    row = await _one(db, """
        select
          (select count(*) from ehs_documents where status = 'published')     as published,
          (select count(*) from ehs_documents
            where status = 'published' and review_due_on < current_date)      as review_overdue,
          (select count(*) from ehs_chemicals where is_active)                as chemicals,
          (select count(*) from ehs_chemicals c where c.is_active
             and exists (select 1 from ehs_sds s
                          where s.chemical_id = c.id and s.is_current))       as with_sds,
          (select count(*) from ehs_sds s
             join ehs_chemicals c on c.id = s.chemical_id
            where s.is_current and c.is_active
              and s.review_due_on < current_date)                             as sds_overdue
    """, {})
    published, review_overdue, chemicals, with_sds, sds_overdue = row
    return {
        "policies_published": published, "review_overdue": review_overdue,
        "chemicals": chemicals, "with_current_sds": with_sds,
        "sds_coverage_rate": _rate(with_sds, chemicals),
        "sds_review_overdue": sds_overdue,
    }


async def high_risk_work(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          (select count(*) from ehs_permits
            where issued_at >= :start and issued_at < :end)                   as issued,
          (select count(*) from ehs_permits
            where issued_at >= :start and issued_at < :end
              and closed_at is not null)                                      as closed,
          (select count(*) from ehs_permits
            where issued_at >= :start and issued_at < :end
              and status = 'expired' and closed_at is null)                   as expired_open,
          (select count(*) from ehs_jsa where status = 'active')              as active_jsa,
          (select count(*) from ehs_hazards
            where status <> 'eliminated')                                     as open_hazards,
          (select count(*) from ehs_hazards
            where status <> 'eliminated' and lower(coalesce(current_rating,'')) = 'critical')
                                                                              as critical_hazards
    """, period.params)
    issued, closed, expired_open, jsa, hazards, critical = row
    return {
        "permits_issued": issued, "permits_closed": closed,
        # The one that matters: a permit that ran out with nobody confirming
        # the area was left safe.
        "permits_expired_without_closeout": expired_open,
        "permit_closeout_rate": _rate(closed, issued),
        "active_job_analyses": jsa,
        "open_hazards": hazards, "critical_hazards": critical,
    }


async def emergency(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          count(*)                                                      as drills,
          count(*) filter (where status = 'evaluated')                  as evaluated,
          count(*) filter (where all_accounted_for is false)            as muster_failed,
          count(*) filter (where all_accounted_for is null
                             and status <> 'planned')                   as muster_unanswered,
          avg(evacuation_seconds) filter (where evacuation_seconds is not null) as avg_seconds
        from ehs_drills
       where held_on >= :start and held_on < :end
    """, period.params)
    drills, evaluated, failed, unanswered, avg_seconds = row

    from app.crud.drill import coverage as responder_coverage

    cover = await responder_coverage(db)
    return {
        "drills_held": drills, "evaluated": evaluated,
        "muster_failed": failed, "muster_unanswered": unanswered,
        "average_evacuation_seconds": round(float(avg_seconds)) if avg_seconds else None,
        "current_first_aiders": cover["current"],
        "lapsed_first_aiders": len(cover["lapsed"]),
    }


async def toolbox(db: AsyncSession, period: Period) -> dict:
    row = await _one(db, """
        select
          (select count(*) from ehs_toolbox_talks
            where period_month >= :start and period_month < :end)            as talks,
          (select count(*) from ehs_tbt_deliveries d
             join ehs_toolbox_talks t on t.id = d.talk_id
            where t.period_month >= :start and t.period_month < :end)        as deliveries,
          (select count(*) from ehs_tbt_deliveries d
             join ehs_toolbox_talks t on t.id = d.talk_id
            where t.period_month >= :start and t.period_month < :end
              and d.status = 'delivered')                                    as delivered
    """, period.params)
    talks, deliveries, delivered = row
    return {
        "topics": talks, "deliveries": deliveries, "delivered": delivered,
        "completion_rate": _rate(delivered, deliveries),
    }


async def build_pack(db: AsyncSession, *, date_from: date, date_to: date) -> dict:
    """The whole pack, in one call.

    One call because an auditor is handed one document, and because assembling
    it across nine round trips invites a page that renders eight sections and
    silently drops the ninth.
    """
    period = Period(date_from, date_to)
    return {
        "period": {"from": date_from.isoformat(), "to": date_to.isoformat()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "incidents": await incidents(db, period),
        "statutory": await statutory(db, period),
        "actions": await actions(db, period),
        "training": await training(db, period),
        "committee": await committee(db, period),
        "inspections": await inspections(db, period),
        "documents": await documents(db, period),
        "high_risk_work": await high_risk_work(db, period),
        "emergency": await emergency(db, period),
        "toolbox": await toolbox(db, period),
    }
