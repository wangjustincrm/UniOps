"""Pydantic schema for the structured badge configuration.

Validates the JSONB stored in `vms_config.badge_config`. Defaults live in
`app.services.badge_config.DEFAULT_BADGE_CONFIG`; this module enforces shape,
hex colors, the meta-field whitelist, and the alignment enum.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator

from app.services.badge_config import META_FIELD_KEYS

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _check_hex(v: str) -> str:
    if not _HEX.match(v):
        raise ValueError(f"invalid hex color: {v!r} (expected #RRGGBB)")
    return v


class AreaStyle(BaseModel):
    label: str
    bg: str
    fg: str
    risk: str

    @field_validator("bg", "fg")
    @classmethod
    def _hex(cls, v: str) -> str:
        return _check_hex(v)


class Band(BaseModel):
    title: str
    show_zone: bool = True
    show_risk: bool = True
    risk_suffix: str = "RISK"
    areas: dict[str, AreaStyle]


class Identity(BaseModel):
    name_uppercase: bool = True
    show_company: bool = True


class MetaField(BaseModel):
    key: str
    label: str
    visible: bool = True

    @field_validator("key")
    @classmethod
    def _known_key(cls, v: str) -> str:
        if v not in META_FIELD_KEYS:
            raise ValueError(f"unknown meta field key: {v!r}")
        return v


class Qr(BaseModel):
    show: bool = True
    label: str = ""


class Footer(BaseModel):
    show: bool = True
    lines: list[str] = []


class Style(BaseModel):
    name_size_pt: int = 22
    band_title_size_pt: int = 18
    text_color: str = "#1A2730"
    company_color: str = "#4E6070"
    footer_color: str = "#4E6070"
    name_align: Literal["left", "center", "right"] = "left"

    @field_validator("text_color", "company_color", "footer_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        return _check_hex(v)


class BadgeConfig(BaseModel):
    version: int = 1
    band: Band
    identity: Identity
    meta_fields: list[MetaField]
    qr: Qr
    footer: Footer
    style: Style
