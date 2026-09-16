"""Drawing a QR code as SVG.

SVG rather than PNG because this image has to be produced on a server with no
rasteriser — reportlab's renderPM needs one and it is not installed — and
because a code that is going to be printed and stuck on a wall should scale
without going soft.

The matrix comes from reportlab's encoder, which is already a dependency and
already produces the codes on the chemical labels. Only the drawing is ours:
rendering the widget through renderSVG produces one rect per dark module, some
44 kB for a short URL, which on a settings page listing forty areas is most of
a megabyte of identical machinery. Runs of dark modules are merged into one
path instead, which is about twenty times smaller.
"""
from __future__ import annotations

from reportlab.graphics.barcode import qr

# Four modules of white around the code. This is the specification's minimum,
# not decoration: a reader needs it to find the edges, and a code printed hard
# against a border is the usual reason a phone will not see one.
QUIET_ZONE = 4


def svg(data: str, *, size_px: int = 240, level: str = "M") -> str:
    """One QR code, as a standalone SVG document."""
    widget = qr.QrCodeWidget(data, barLevel=level)
    widget.draw()
    code = widget.qr
    n = code.getModuleCount()
    span = n + QUIET_ZONE * 2

    segments: list[str] = []
    for row in range(n):
        col = 0
        while col < n:
            if not code.isDark(row, col):
                col += 1
                continue
            start = col
            while col < n and code.isDark(row, col):
                col += 1
            width = col - start
            segments.append(
                f"M{start + QUIET_ZONE} {row + QUIET_ZONE}h{width}v1h-{width}z")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size_px}" height="{size_px}" '
        f'viewBox="0 0 {span} {span}" shape-rendering="crispEdges" role="img">'
        f'<rect width="{span}" height="{span}" fill="#ffffff"/>'
        f'<path d="{"".join(segments)}" fill="#000000"/>'
        '</svg>'
    )
