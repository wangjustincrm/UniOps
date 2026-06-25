"""Schema dataclasses describing how a managed entity is rendered/edited."""
from __future__ import annotations

from dataclasses import dataclass, field

FieldType = str  # "string"|"number"|"decimal"|"bool"|"date"|"datetime"|"uuid"|"json"|"enum"


@dataclass
class FieldSpec:
    name: str
    type: FieldType
    editable: bool
    label: str | None = None
    options: list[str] | None = None  # for enum types

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "editable": self.editable,
            "label": self.label or self.name,
            "options": self.options,
        }


@dataclass
class EntitySchema:
    key: str
    label: str
    number_field: str
    list_columns: list[str]
    search_fields: list[str]
    order_by: str
    fields: list[FieldSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "number_field": self.number_field,
            "list_columns": self.list_columns,
            "search_fields": self.search_fields,
            "order_by": self.order_by,
            "fields": [f.to_dict() for f in self.fields],
        }

    def editable_field_names(self) -> set[str]:
        return {f.name for f in self.fields if f.editable}

    def field_type(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.type
        return None
