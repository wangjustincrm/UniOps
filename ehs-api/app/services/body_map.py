"""Where each entry on the body-part list sits on the dashboard's figure.

Round 10, q10-body: "draw a front and back outline of a body and shade each
part by how many injuries it had, using the body part list under Settings →
Lists."

The list is HSE's and grows, so the figure cannot know every entry. The
entries the module was seeded with are placed by their code; any entry can be
placed (or moved, or kept off the figure) under Settings → Lists, which stores
it in the entry's `attrs.body_region`. An entry with no place — and a part
typed in free text on an old report — is listed beside the figure as "not on
the picture" rather than guessed into a region.
"""
from __future__ import annotations

# The regions the figure draws, in the order the Lists chooser offers them.
REGIONS: tuple[tuple[str, str], ...] = (
    ("head", "Head"), ("face", "Face"), ("eye", "Eye"), ("ear", "Ear"),
    ("mouth", "Mouth / teeth"), ("neck", "Neck"), ("shoulder", "Shoulder"),
    ("chest", "Chest"), ("abdomen", "Abdomen"), ("back", "Back"),
    ("arm", "Arm"), ("elbow", "Elbow"), ("wrist", "Wrist"), ("hand", "Hand / finger"),
    ("hip", "Hip"), ("leg", "Leg"), ("knee", "Knee"), ("ankle", "Ankle"), ("foot", "Foot / toe"),
)
REGION_KEYS = {k for k, _ in REGIONS}
# Stored to say "this entry is deliberately not on the figure".
OFF_PICTURE = "none"

# The seeded list, by code. Lower-cased so a code typed in capitals still lands.
_SEEDED = {
    "head": "head", "face": "face", "eye": "eye", "ear": "ear",
    "mouth": "mouth", "teeth": "mouth", "neck": "neck", "shoulder": "shoulder",
    "chest": "chest", "abdomen": "abdomen", "back": "back", "arm": "arm",
    "elbow": "elbow", "wrist": "wrist", "hand": "hand", "finger": "hand",
    "hip": "hip", "leg": "leg", "knee": "knee", "ankle": "ankle", "foot": "foot",
    "toe": "foot",
}


def region_for(code: str | None, attrs: dict | None) -> str | None:
    """The region an entry is drawn in, or None when it is not on the figure."""
    chosen = (attrs or {}).get("body_region")
    if chosen == OFF_PICTURE:
        return None
    if chosen in REGION_KEYS:
        return chosen
    return _SEEDED.get((code or "").lower())


def valid_choice(value: object) -> bool:
    return value is None or value == OFF_PICTURE or value in REGION_KEYS
