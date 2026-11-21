"""Shared type aliases for JSON payloads, model fields, and Django signals."""

from __future__ import annotations

from datetime import date
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from typing import Literal
from typing import TypedDict

if TYPE_CHECKING:
    from django.db.models import Model

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)
type JsonObject = dict[str, JsonValue]
type ModelFieldValue = JsonValue | date | datetime | Decimal | Model
type M2MAction = Literal[
    "pre_add",
    "post_add",
    "pre_remove",
    "post_remove",
    "pre_clear",
    "post_clear",
]


class ModelSignalKwargs(TypedDict, total=False):
    """Keyword arguments sent with Django save and delete signals."""

    created: bool
    raw: bool
    using: str
    update_fields: frozenset[str] | None


class M2MChangedKwargs(TypedDict, total=False):
    """Keyword arguments sent with Django m2m_changed signals."""

    action: M2MAction
    model: type[Model]
    pk_set: set[int] | None
    using: str
