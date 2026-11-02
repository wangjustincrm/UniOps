"""997 functional acknowledgements (Appendix A.5, FR-OB-22a)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.x12 import X12Error, control, segments

ACCEPTED_CODES = {"A", "E"}  # A.5: A or E accepted; P, R or anything else rejected


@dataclass
class SetAck:
    type: str              # AK2-01, e.g. 940
    control: str           # AK2-02, leading zeros dropped
    code: str | None       # AK5-01
    reasons: list[str] = field(default_factory=list)  # AK5-02…

    @property
    def accepted(self) -> bool:
        return (self.code or "") in ACCEPTED_CODES


@dataclass
class GroupAck:
    functional_id: str     # AK1-01 (OW for 940 / 943)
    group_control: str     # AK1-02 = our GS06, leading zeros dropped
    code: str | None       # AK9-01
    sets: list[SetAck] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return (self.code or "") in ACCEPTED_CODES


def parse_997(data: bytes) -> list[GroupAck]:
    """Every AK1 group in the file with its AK2/AK5 sets and AK9 code."""
    segs = segments(data)
    if not any(s[0] == "ST" and len(s) > 1 and s[1] == "997" for s in segs):
        raise X12Error("not a 997 (no ST*997)")
    groups: list[GroupAck] = []
    cur_set: SetAck | None = None
    for s in segs:
        if s[0] == "AK1" and len(s) >= 3:
            groups.append(GroupAck(functional_id=s[1], group_control=control(s[2]), code=None))
            cur_set = None
        elif s[0] == "AK2" and len(s) >= 3 and groups:
            cur_set = SetAck(type=s[1], control=control(s[2]), code=None)
            groups[-1].sets.append(cur_set)
        elif s[0] == "AK5" and cur_set is not None:
            cur_set.code = s[1] if len(s) > 1 else None
            cur_set.reasons = [x for x in s[2:] if x]
        elif s[0] == "AK9" and groups:
            groups[-1].code = s[1] if len(s) > 1 else None
    if not groups:
        raise X12Error("a 997 without AK1")
    return groups
