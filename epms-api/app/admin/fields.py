"""Schema dataclasses describing how a managed entity is rendered/edited."""
from __future__ import annotations

from dataclasses import dataclass, field

# adds "reference"      — a FK the admin edits via a picker (id + denormalized name)
#      "reference_list"  — a JSONB array of ids edited via a multi-picker. Not a FK:
#                          payment_applications.invoice_ids / gr_ids are bare id
#                          arrays with nothing enforcing them, which is exactly why
#                          they need a repair path here.
FieldType = str  # "string"|"number"|"decimal"|"bool"|"date"|"datetime"|"uuid"|"json"|"enum"|"reference"|"reference_list"


@dataclass
class FieldSpec:
    name: str
    type: FieldType
    editable: bool
    label: str | None = None
    options: list[str] | None = None          # for enum types
    ref_source: str | None = None             # for reference: resolver key ("users"|"vendors"|"cost_centers")
    ref_name_field: str | None = None         # for reference: denormalized name column kept in sync

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "editable": self.editable,
            "label": self.label or self.name,
            "options": self.options,
            "ref_source": self.ref_source,
            "ref_name_field": self.ref_name_field,
        }


@dataclass
class ChildSchema:
    """A one-level child collection (line items) editable inline with the parent."""
    table_label: str
    model: type
    fk_field: str                              # child column pointing back at the parent id
    fields: list[FieldSpec] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "table_label": self.table_label,
            "fk_field": self.fk_field,
            "fields": [f.to_dict() for f in self.fields],
        }

    def editable_field_names(self) -> set[str]:
        return {f.name for f in self.fields if f.editable}

    def field_type(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.type
        return None


@dataclass
class EntitySchema:
    key: str
    label: str
    number_field: str
    list_columns: list[str]
    search_fields: list[str]
    order_by: str
    fields: list[FieldSpec] = field(default_factory=list)
    allow_edit: bool = True                    # False = delete-only entity (no PATCH)
    child: ChildSchema | None = None           # line-item sub-collection (None = none)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "number_field": self.number_field,
            "list_columns": self.list_columns,
            "search_fields": self.search_fields,
            "order_by": self.order_by,
            "allow_edit": self.allow_edit,
            "fields": [f.to_dict() for f in self.fields],
            "child": self.child.to_dict() if self.child else None,
        }

    def editable_field_names(self) -> set[str]:
        return {f.name for f in self.fields if f.editable}

    def field_type(self, name: str) -> str | None:
        for f in self.fields:
            if f.name == name:
                return f.type
        return None

    def field_spec(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None
