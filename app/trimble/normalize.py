from datetime import datetime, timezone

from app.trimble.client import Region

CLOSED_TOPIC_STATUSES = {"closed", "done", "resolved"}

_PROJECT_FIELDS = ("id", "name", "description", "rootId", "location", "createdOn", "modifiedOn", "updatedOn",
                   "lastVisitedOn", "access", "size", "filesCount", "usersCount", "address")
_ITEM_FIELDS = ("id", "name", "type", "size", "versionId", "createdOn", "modifiedOn", "createdBy", "modifiedBy",
                "parentId")
_TOPIC_FIELDS = ("guid", "title", "topic_type", "topic_status", "priority", "assigned_to", "due_date",
                 "creation_date", "creation_author", "modified_date", "labels", "stage")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pick(src: dict, fields: tuple[str, ...]) -> dict:
    return {k: src[k] for k in fields if k in src and src[k] is not None}


def _person(v):
    if isinstance(v, dict):
        return _pick(v, ("id", "email", "firstName", "lastName"))
    return v


def envelope(data: dict, *, project_id: str | None = None, region: Region | None = None) -> dict:
    out = {"source": "trimble_connect", "fetched_at": now_iso()}
    if project_id:
        out["project_id"] = project_id
    if region:
        out["region"] = region.name
    return {**out, **data}


def project_summary(p: dict) -> dict:
    out = _pick(p, _PROJECT_FIELDS)
    if "_region" in p:
        out["region"] = p["_region"]
    return out


def folder_item(i: dict) -> dict:
    out = _pick(i, _ITEM_FIELDS)
    for k in ("createdBy", "modifiedBy"):
        if k in out:
            out[k] = _person(out[k])
    return out


def topic(t: dict) -> dict:
    return _pick(t, _TOPIC_FIELDS)


def is_open_topic(t: dict) -> bool:
    return str(t.get("topic_status") or "").strip().lower() not in CLOSED_TOPIC_STATUSES
