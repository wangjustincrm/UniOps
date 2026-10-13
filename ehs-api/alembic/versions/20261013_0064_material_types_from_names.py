"""Give the session files 0061 brought in a type, from their names.

0061 moved each session's old single file into the materials table with the
type unknown ('application/octet-stream'), because the old column kept none.
The browser opens only known safe types in a tab, so those PDFs downloaded
instead of opening. Raised in review by uniops-93. Guessed from the extension
the file was uploaded with; anything else stays unknown and downloads, which
is the safe answer.

Revision ID: 20261013_0064
Revises: 20261013_0063
"""
from alembic import op

revision = "20261013_0064"
down_revision = "20261013_0063"
branch_labels = None
depends_on = None

_BY_EXTENSION = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
    "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif",
    "mp4": "video/mp4", "webm": "video/webm", "txt": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def upgrade() -> None:
    for ext, ctype in _BY_EXTENSION.items():
        op.execute(f"""
            UPDATE ehs_course_materials SET content_type = '{ctype}'
             WHERE content_type = 'application/octet-stream'
               AND file_id IS NOT NULL
               AND lower(filename) LIKE '%.{ext}'
        """)


def downgrade() -> None:
    # The types were unknown before; there is nothing to put back that was known.
    pass
