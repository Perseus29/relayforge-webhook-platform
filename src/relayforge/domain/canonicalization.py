"""Canonical payload hashing and deterministic request identity.

The canonical form is a contract: changing it can make a previously accepted request
look new, or make two semantically different requests collide. The implementation avoids
Python's default repr(), locale-sensitive formatting, and unordered mapping iteration.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from relayforge.domain.errors import ValidationError


def _normalize(value: Any, path: str = "$", depth: int = 0) -> Any:
    if depth > 64:
        raise ValidationError("Payload nesting exceeds 64 levels", path=path)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("Non-finite numbers are not valid JSON", path=path)
        return 0 if value == 0 else value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("Non-finite decimal is not valid", path=path)
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValidationError("Datetime values must include a timezone", path=path)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise ValidationError("Object keys must be strings", path=path)
            normalized[key] = _normalize(value[key], f"{path}.{key}", depth + 1)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item, f"{path}[{index}]", depth + 1) for index, item in enumerate(value)]
    raise ValidationError("Unsupported payload value", path=path, value_type=type(value).__name__)


def canonical_json(value: Any) -> str:
    normalized = _normalize(value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_key(*parts: str) -> str:
    if any(not isinstance(part, str) for part in parts):
        raise TypeError("stable key parts must be strings")
    material = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(material).hexdigest()

