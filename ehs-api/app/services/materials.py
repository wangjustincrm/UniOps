"""Training material: files and links, on a course or on one session of it.

Round 6, TC-06: "When the training session is created, if there are
attachments attached in the training course, transfer a copy over to the
session. add the ability to add a url and a video, all file types would be
good."

Three decisions worth stating:

**A session's copy is the same file, referenced again.** Copying the bytes in
the file server would double the storage of every deck for every sitting. So a
file is deleted from the file server only when nothing references it any more —
not the course's row, not any session's copy, not the old single-file columns.

**Any file type, except what can run.** HTML, SVG and script are refused: the
module serves what it stores, and a file that can carry script is a page, not a
handout. Everything else goes, up to the file server's own limit.

**A video is usually a link.** The file server takes 50 MB; most training video
is larger, and already lives somewhere (SharePoint, a vendor's site). A link is
a first-class item, and only http and https are accepted — `javascript:` in a
link someone clicks is the same hole as a script in a file.
"""
from __future__ import annotations

import uuid
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import CourseDelivery, CourseMaterial
from app.models.training import Course
from app.services.attachments import delete_from_file_server, upload_to_file_server

# The file server's own ceiling; refusing here says so before the upload is
# sent rather than after.
MAX_MATERIAL_SIZE = 50 * 1024 * 1024

# Types and extensions that a browser would run rather than show.
BLOCKED_TYPES = {
    "text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml",
    "application/javascript", "text/javascript", "application/ecmascript",
    "application/x-msdownload", "application/x-sh", "application/x-bat",
}
BLOCKED_EXTENSIONS = {
    ".html", ".htm", ".xhtml", ".svg", ".xml", ".js", ".mjs", ".exe", ".bat", ".cmd",
    ".com", ".msi", ".scr", ".sh", ".ps1", ".vbs", ".jar", ".hta",
    # Web archives and other pages a browser renders; programs Windows runs.
    ".mht", ".mhtml", ".xht", ".shtml", ".svgz", ".wsf", ".pif", ".cpl", ".reg",
    ".lnk", ".py",
}


# ★ Round 7, TC-05: what the file server will actually keep for Safety — its
# own list plus the slides, video and audio it accepts from this service only
# (file-api, SERVICE_EXTRA_CONTENT_TYPES). "Any file but a page or a program"
# was promised here, and a 19 MB video came back as the file server's raw 415.
# Said here, before the upload, in words; the extension check above still
# applies, because the type is whatever the browser claimed.
ALLOWED_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/heic",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
    "text/csv", "text/plain",
    "video/mp4", "video/quicktime", "video/webm",
    "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/wav", "audio/x-wav",
}
ACCEPTED = ("a PDF, a Word, Excel or PowerPoint file, a picture, a text file, "
            "or a video or sound recording (MP4, MOV, WebM, MP3, M4A, WAV)")


def _blocked(filename: str, ctype: str) -> bool:
    name = filename.lower()
    return ctype in BLOCKED_TYPES or any(name.endswith(ext) for ext in BLOCKED_EXTENSIONS)


async def add_file(
    db: AsyncSession, file: UploadFile, *, course_id: uuid.UUID | None = None,
    delivery_id: uuid.UUID | None = None, record_id: uuid.UUID | None = None,
    user_id: uuid.UUID, user_name: str | None, token: str,
) -> CourseMaterial:
    data = await file.read()
    if len(data) > MAX_MATERIAL_SIZE:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {MAX_MATERIAL_SIZE // 1024 // 1024} MB. "
            "Add a link to where it is kept instead — that is how a long video is attached.")
    ctype = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    name = file.filename or "material"
    if _blocked(name, ctype):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"'{name}' is a web page or a program, which this cannot store. "
            "Save it as a PDF, or add a link to it.")
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"'{name}' cannot be stored here. Attach {ACCEPTED} — or add a link to it.")
    row = CourseMaterial(
        id=uuid.uuid4(), course_id=course_id, delivery_id=delivery_id, record_id=record_id,
        file_id=await upload_to_file_server(
            data, name, ctype, "ehs_crs", delivery_id or course_id or record_id, token),
        filename=name, content_type=ctype, file_size=len(data),
        uploaded_by=user_id, uploaded_by_name=user_name,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


def check_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A link must be a web address starting with http:// or https://")
    if len(url) > 2000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That address is too long")
    return url


async def add_link(
    db: AsyncSession, url: str, title: str | None, *, course_id: uuid.UUID | None = None,
    delivery_id: uuid.UUID | None = None, record_id: uuid.UUID | None = None,
    user_id: uuid.UUID, user_name: str | None,
) -> CourseMaterial:
    url = check_url(url)
    row = CourseMaterial(
        id=uuid.uuid4(), course_id=course_id, delivery_id=delivery_id, record_id=record_id,
        url=url, filename=(title or "").strip()[:255] or urlparse(url).netloc,
        content_type="text/uri-list", file_size=0,
        uploaded_by=user_id, uploaded_by_name=user_name,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def copy_course_to_session(
    db: AsyncSession, course_id: uuid.UUID | None, delivery_id: uuid.UUID,
) -> int:
    """Give a new session its course's material. Returns how many."""
    if course_id is None:
        return 0
    rows = (await db.execute(
        select(CourseMaterial).where(CourseMaterial.course_id == course_id)
        .order_by(CourseMaterial.created_at))).scalars().all()
    for m in rows:
        db.add(CourseMaterial(
            id=uuid.uuid4(), delivery_id=delivery_id, file_id=m.file_id, url=m.url,
            filename=m.filename, content_type=m.content_type, file_size=m.file_size,
            uploaded_by=m.uploaded_by, uploaded_by_name=m.uploaded_by_name,
        ))
    await db.flush()
    return len(rows)


async def file_still_used(db: AsyncSession, file_id: uuid.UUID, *, besides: uuid.UUID) -> bool:
    """Whether anything other than row `besides` still points at this file."""
    others = (await db.execute(
        select(func.count()).select_from(CourseMaterial)
        .where(CourseMaterial.file_id == file_id, CourseMaterial.id != besides))).scalar() or 0
    legacy = (await db.execute(
        select(func.count()).select_from(Course).where(Course.material_file_id == file_id)
    )).scalar() or 0
    legacy += (await db.execute(
        select(func.count()).select_from(CourseDelivery)
        .where(CourseDelivery.material_file_id == file_id))).scalar() or 0
    return bool(others or legacy)


async def remove(db: AsyncSession, row: CourseMaterial, token: str) -> None:
    """Take an item off. The file itself goes only when nothing else uses it."""
    if row.file_id is not None and not await file_still_used(db, row.file_id, besides=row.id):
        await delete_from_file_server(row.file_id, token)
    await db.delete(row)
    await db.flush()
