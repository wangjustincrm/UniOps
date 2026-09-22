"""Reopen read-and-sign tasks that somebody else's signature closed.

A one-off repair for the defect fixed alongside it: closing the task behind a
document acknowledgement was scoped to the document and not to the person, so
the first signature on a policy closed the outstanding task for everyone it had
been assigned to.

The acknowledgement rows stayed correct, which makes what to reopen knowable
exactly rather than guessable: a closed task whose assignee has no
acknowledgement for that document, on a document that is still published.
Tasks closed by the person who actually signed are left alone, and the dry run
prints both counts so the split can be read before anything is written.

Dry run by default; APPLY=1 to write. Run it inside the ehs-api container, so
it uses that container's database — the host environment points at production.
"""
import asyncio, os, asyncpg

DRY = os.environ.get("APPLY") != "1"

async def main():
    c = await asyncpg.connect(host="postgres", user="epms",
                              password=os.environ["POSTGRES_PASSWORD"], database="epms")
    # Closed read-and-sign tasks whose assignee has no acknowledgement for that
    # document, on a document that is still published. Those were closed by
    # somebody else's signature, not by the person doing anything.
    rows = await c.fetch("""
        select t.id, t.assigned_user_id, t.document_id, t.title
          from tasks t
          join ehs_documents d on d.id = t.document_id
         where t.document_type = 'ehs_doc'
           and t.is_completed = true
           and d.status = 'published'
           and not exists (
                 select 1 from ehs_document_acks a
                  where a.document_id = t.document_id
                    and a.user_id = t.assigned_user_id)
    """)
    print(f"被别人的签字误关的任务: {len(rows)}")
    # The opposite set, as the positive control: closed AND acknowledged is
    # correct and must not be touched.
    ok = await c.fetchval("""
        select count(*) from tasks t
         where t.document_type='ehs_doc' and t.is_completed = true
           and exists (select 1 from ehs_document_acks a
                        where a.document_id=t.document_id and a.user_id=t.assigned_user_id)
    """)
    print(f"确实由本人签字而关闭的(不动): {ok}")

    if DRY:
        print("\n[dry run] 未写入。加 APPLY=1 才执行。")
        return
    n = await c.execute("""
        update tasks t
           set is_completed = false, completed_at = null, completed_by = null
          from ehs_documents d
         where d.id = t.document_id
           and t.document_type = 'ehs_doc'
           and t.is_completed = true
           and d.status = 'published'
           and not exists (
                 select 1 from ehs_document_acks a
                  where a.document_id = t.document_id
                    and a.user_id = t.assigned_user_id)
    """)
    print("已重开:", n)
    print("现在未完成的文件任务:", await c.fetchval(
        "select count(*) from tasks where document_type='ehs_doc' and is_completed=false"))

asyncio.run(main())
