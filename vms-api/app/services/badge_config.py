"""Default badge configuration + deep-merge helper.

Single source of truth for what the badge looks like out of the box. The
defaults reproduce the historical hard-coded BadgePreview exactly, so an
empty/partial stored config renders identically to the pre-config badge.
"""
from __future__ import annotations

import copy

# Whitelisted meta-field keys. Each maps to a known visit/visitor accessor in
# the frontend renderer. Admins may toggle/relabel/reorder these but cannot
# bind arbitrary data.
META_FIELD_KEYS = {
    "visit_date",
    "host",
    "valid_until",
    "vehicle_plate",
    "accompanying_count",
    "visit_purpose",
}

DEFAULT_BADGE_CONFIG: dict = {
    "version": 1,
    "band": {
        "title": "VISITOR",
        "show_zone": True,
        "show_risk": True,
        "risk_suffix": "RISK",
        "areas": {
            "office":             {"label": "OFFICE",           "bg": "#10B981", "fg": "#FFFFFF", "risk": "LOW"},
            "warehouse":          {"label": "WAREHOUSE",        "bg": "#F59E0B", "fg": "#1A2730", "risk": "MEDIUM"},
            "production_non_gmp": {"label": "PRODUCTION",       "bg": "#EA580C", "fg": "#FFFFFF", "risk": "MEDIUM"},
            "production_gmp":     {"label": "PRODUCTION (GMP)", "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH"},
            "laboratory":         {"label": "LABORATORY",       "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH"},
            "all":                {"label": "ALL ZONES",        "bg": "#DC2626", "fg": "#FFFFFF", "risk": "HIGH"},
        },
    },
    "identity": {"name_uppercase": True, "show_company": True},
    "meta_fields": [
        {"key": "visit_date",         "label": "Date",         "visible": True},
        {"key": "host",               "label": "Host",         "visible": True},
        {"key": "valid_until",        "label": "Valid until",  "visible": True},
        {"key": "vehicle_plate",      "label": "Vehicle",      "visible": False},
        {"key": "accompanying_count", "label": "Accompanying", "visible": False},
        {"key": "visit_purpose",      "label": "Purpose",      "visible": False},
    ],
    "qr": {"show": True, "label": "Scan to check out"},
    "footer": {
        "show": True,
        "lines": [
            "Must be accompanied by Host at all times",
            "Please return badge when leaving",
        ],
    },
    "style": {
        "name_size_pt": 22,
        "band_title_size_pt": 18,
        "text_color": "#1A2730",
        "company_color": "#4E6070",
        "footer_color": "#4E6070",
        "name_align": "left",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` onto a deep copy of `base`.

    Dicts merge key-by-key; every other type (including lists) replaces the
    base value wholesale. Never mutates either argument.
    """
    result = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result
