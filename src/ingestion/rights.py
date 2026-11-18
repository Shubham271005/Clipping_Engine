"""Copyright / permission tracking. Nothing is published unless rights are explicit."""
from __future__ import annotations

from .. import db
from ..common import cfg

VALID = {"unknown", "owned", "permission_granted", "denied"}


def set_rights(source_id: str, status: str, commercial: bool, authorized_on: str | None = None,
               ref: str | None = None, restrictions: str | None = None,
               streamer: str | None = None, game: str | None = None) -> dict:
    if status not in VALID:
        raise ValueError(f"rights status must be one of {sorted(VALID)}")
    if not db.one("SELECT id FROM sources WHERE id=?", (source_id,)):
        raise ValueError(f"unknown source {source_id}")
    if status == "permission_granted" and not ref:
        raise ValueError("permission_granted requires --ref (where the written permission is stored)")
    db.execute(
        "UPDATE sources SET rights_status=?, commercial_use=?, authorized_on=?, authorization_ref=?,"
        " restrictions=?, streamer=COALESCE(?,streamer), game=COALESCE(?,game) WHERE id=?",
        (status, 1 if commercial else 0, authorized_on or db.now_iso()[:10], ref, restrictions,
         streamer, game, source_id))
    return db.one("SELECT * FROM sources WHERE id=?", (source_id,))


def publish_allowed(source: dict) -> tuple[bool, str]:
    st = source.get("rights_status") or "unknown"
    if st not in ("owned", "permission_granted"):
        return False, f"rights status is '{st}' - set rights before publishing"
    if cfg()["publishing"]["require_commercial_permission"] and not source.get("commercial_use"):
        return False, "no explicit commercial-use permission recorded"
    return True, "ok"


def restriction_blocks(source: dict, platform: str) -> str | None:
    """Very explicit restrictions syntax: 'no-youtube', 'no-instagram', 'no-publish'."""
    r = (source.get("restrictions") or "").lower()
    if "no-publish" in r or f"no-{platform}" in r:
        return f"restriction '{source['restrictions']}' forbids {platform}"
    return None
