"""Employer identity helpers used by ingestion and evidence aggregation.

Channels are not employers.  These helpers deliberately keep the channel
(`platform`) separate from the employer independence key used by the
cross-validation gate.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Mapping


_LEGAL_SUFFIXES = (
    "有限责任公司", "股份有限公司", "集团有限公司", "控股有限公司",
    "有限公司", "公司",
)
_BRANCH_SUFFIX = re.compile(r"(?:分公司|事业部|招聘中心)$")
_SPACES_AND_PUNCT = re.compile(r"[\s·•・,，。._\-—]+")
_BRACKET_QUALIFIER = re.compile(r"[（(](?:中国|总部|招聘|校招|社招|\w{2,12})[）)]")
_UNKNOWN_NAMES = {"", "未知", "不详", "保密", "某公司", "某科技公司", "unknown", "n/a", "-"}


def normalize_employer_name(name: str | None) -> str:
    """Return a conservative comparison form for an employer name.

    Legal suffixes and harmless recruiting qualifiers are removed.  Brand
    names are not guessed and subsidiaries are not merged unless an alias or
    parent identity is explicitly supplied.
    """
    value = unicodedata.normalize("NFKC", name or "").strip()
    if value.lower() in _UNKNOWN_NAMES:
        return ""
    value = _BRACKET_QUALIFIER.sub("", value)
    value = _SPACES_AND_PUNCT.sub("", value).strip()
    value = _BRANCH_SUFFIX.sub("", value)
    for suffix in _LEGAL_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            value = value[: -len(suffix)]
            break
    return value.casefold()


def resolve_employer_name(
    name: str | None,
    aliases: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve an employer alias to its canonical display name."""
    normalized = normalize_employer_name(name)
    if not normalized:
        return None
    alias_index = {
        normalize_employer_name(alias): canonical
        for alias, canonical in (aliases or {}).items()
        if normalize_employer_name(alias)
    }
    return alias_index.get(normalized) or (name or "").strip()


def stable_employer_id(name: str | None, aliases: Mapping[str, str] | None = None) -> str | None:
    """Create a deterministic external identity; unknown names stay unknown."""
    canonical = resolve_employer_name(name, aliases)
    normalized = normalize_employer_name(canonical)
    if not normalized:
        return None
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"emp_{digest}"


def employer_independence_key(meta: Mapping | None) -> str | None:
    """Return the entity counted by the independent-employer gate.

    Subsidiaries with a known group/parent share one independence key.  An
    unidentified employer returns ``None`` and therefore cannot satisfy the
    multi-employer gate.
    """
    meta = meta or {}
    for field in ("group_employer_id", "employer_parent_id", "parent_employer_id", "employer_id"):
        value = meta.get(field)
        if value not in (None, ""):
            return str(value)
    return stable_employer_id(meta.get("company") or meta.get("employer_name"))


def get_or_create_employer(db, company: str | None):
    """Resolve/create an ORM Employer when the public model is available.

    The function is intentionally compatible with pre-migration test schemas:
    callers receive ``None`` when Employer has not been introduced yet.
    """
    if not normalize_employer_name(company):
        return None
    from .. import models

    employer_model = getattr(models, "Employer", None)
    alias_model = getattr(models, "EmployerAlias", None)
    if employer_model is None:
        return None

    normalized = normalize_employer_name(company)
    employer = db.query(employer_model).filter(
        employer_model.normalized_name == normalized).first()
    if employer is None and alias_model is not None:
        alias = db.query(alias_model).filter(
            alias_model.normalized_alias == normalized).first()
        employer = getattr(alias, "employer", None) if alias else None
        if employer is None and alias:
            employer = db.query(employer_model).filter(employer_model.id == alias.employer_id).first()
    if employer is None:
        employer = employer_model(
            name=(company or "").strip(), normalized_name=normalized, status="active")
        db.add(employer)
        db.flush()
    return employer


def register_employer_alias(db, employer, alias_name: str):
    """Idempotently attach a reviewed alias to an Employer entity."""
    from .. import models

    alias_model = getattr(models, "EmployerAlias", None)
    normalized = normalize_employer_name(alias_name)
    if alias_model is None or not normalized:
        return None
    existing = db.query(alias_model).filter(
        alias_model.normalized_alias == normalized).first()
    if existing:
        if existing.employer_id != employer.id:
            raise ValueError("employer alias is already bound to another entity")
        return existing
    alias = alias_model(employer_id=employer.id, alias=(alias_name or "").strip(),
                        normalized_alias=normalized)
    db.add(alias)
    db.flush()
    return alias
