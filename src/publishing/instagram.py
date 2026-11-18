"""Instagram Reels publishing via the official Instagram API (Instagram Login, graph.instagram.com).

Flow: create REELS container (resumable) -> upload local file to rupload.facebook.com ->
poll container status until FINISHED -> media_publish -> fetch permalink.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ..common import DIRS, cfg, get_logger

log = get_logger("instagram")
TOKEN_FILE = DIRS["secrets"] / "instagram_token.json"
GRAPH = "https://graph.instagram.com"


def _ver() -> str:
    return cfg()["publishing"]["instagram"]["api_version"]


def _token_data() -> dict:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    tok = os.getenv("IG_ACCESS_TOKEN")
    if not tok:
        raise RuntimeError("Instagram not authorized. Put IG_ACCESS_TOKEN in .env then run: python main.py auth instagram")
    return {"access_token": tok}


def _req(method: str, url: str, retries: int = 4, **kw) -> dict:
    for a in range(retries):
        try:
            r = requests.request(method, url, timeout=120, **kw)
        except requests.RequestException as e:
            if a == retries - 1:
                raise
            time.sleep(2 ** (a + 1))
            continue
        if r.status_code >= 500 or r.status_code == 429:
            time.sleep(2 ** (a + 2))
            continue
        try:
            j = r.json()
        except ValueError:
            j = {"raw": r.text}
        if r.status_code >= 400 or "error" in j:
            raise RuntimeError(f"Instagram API {r.status_code}: {json.dumps(j.get('error', j))[:500]}")
        return j
    raise RuntimeError(f"Instagram API failed after {retries} attempts: {url}")


def authorize() -> dict:
    """Validate token, exchange short-lived -> long-lived if possible, store securely."""
    data = _token_data()
    tok = data["access_token"]
    secret = os.getenv("IG_APP_SECRET")
    if secret and not data.get("long_lived"):
        try:
            j = _req("GET", f"{GRAPH}/access_token", params={
                "grant_type": "ig_exchange_token", "client_secret": secret, "access_token": tok})
            tok = j["access_token"]
            data = {"access_token": tok, "long_lived": True,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=j.get("expires_in", 5184000))).isoformat()}
        except RuntimeError as e:
            log.info("Token exchange skipped (%s) - token is probably already long-lived", str(e)[:120])
            data.setdefault("expires_at", (datetime.now(timezone.utc) + timedelta(days=59)).isoformat())
            data["long_lived"] = True
    me = _req("GET", f"{GRAPH}/{_ver()}/me", params={"fields": "user_id,username,account_type",
                                                    "access_token": tok})
    data.update({"user_id": me.get("user_id") or me.get("id"), "username": me.get("username"),
                 "account_type": me.get("account_type")})
    data.setdefault("expires_at", (datetime.now(timezone.utc) + timedelta(days=59)).isoformat())
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if data.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
        log.warning("Account type is %s - Reels publishing needs a Professional (Creator/Business) account",
                    data.get("account_type"))
    return {k: data[k] for k in ("user_id", "username", "account_type", "expires_at")}


def _token() -> tuple[str, str]:
    data = _token_data()
    if "user_id" not in data:
        authorize()
        data = _token_data()
    exp = datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
    if exp and exp - datetime.now(timezone.utc) < timedelta(days=10):
        try:  # refresh long-lived token (must be >24h old, not expired)
            j = _req("GET", f"{GRAPH}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": data["access_token"]})
            data["access_token"] = j["access_token"]
            data["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=j.get("expires_in", 5184000))).isoformat()
            TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            log.info("Instagram token refreshed")
        except RuntimeError as e:
            log.error("Instagram token refresh failed: %s", e)
    return data["access_token"], str(data["user_id"])


def create_and_upload(path: str, caption: str) -> str:
    tok, uid = _token()
    ig = cfg()["publishing"]["instagram"]
    c = _req("POST", f"{GRAPH}/{_ver()}/{uid}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": caption,
        "share_to_feed": "true" if ig["share_to_feed"] else "false", "access_token": tok})
    cid, uri = c["id"], c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{_ver()}/{c['id']}"
    size = Path(path).stat().st_size
    with open(path, "rb") as f:
        _req("POST", uri, data=f, headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                           "file_size": str(size)})
    log.info("Instagram container %s uploaded (%.1f MB)", cid, size / 1e6)
    return cid


def wait_ready(container_id: str, timeout_s: int = 900) -> str:
    tok, _ = _token()
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        j = _req("GET", f"{GRAPH}/{_ver()}/{container_id}",
                 params={"fields": "status_code,status", "access_token": tok})
        sc = j.get("status_code")
        if sc == "FINISHED":
            return sc
        if sc in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Container {container_id} {sc}: {j.get('status')}")
        time.sleep(10)
    raise RuntimeError(f"Container {container_id} not ready after {timeout_s}s")


def publish(path: str, caption: str) -> dict:
    tok, uid = _token()
    cid = create_and_upload(path, caption)
    wait_ready(cid)
    j = _req("POST", f"{GRAPH}/{_ver()}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok})
    mid = j["id"]
    link = _req("GET", f"{GRAPH}/{_ver()}/{mid}", params={"fields": "permalink", "access_token": tok})
    log.info("Instagram published %s", link.get("permalink"))
    return {"id": mid, "url": link.get("permalink"), "container": cid}


def publishing_quota() -> dict:
    tok, uid = _token()
    return _req("GET", f"{GRAPH}/{_ver()}/{uid}/content_publishing_limit",
                params={"fields": "quota_usage,config", "access_token": tok})


def insights(media_id: str) -> dict:
    tok, _ = _token()
    out = {}
    wanted = ["views", "reach", "likes", "comments", "shares", "saved",
              "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]
    try:
        j = _req("GET", f"{GRAPH}/{_ver()}/{media_id}/insights",
                 params={"metric": ",".join(wanted), "access_token": tok})
        items = j.get("data", [])
    except RuntimeError:
        items = []  # one unsupported metric fails the whole call -> fetch individually
        for m in wanted:
            try:
                items += _req("GET", f"{GRAPH}/{_ver()}/{media_id}/insights",
                              params={"metric": m, "access_token": tok}).get("data", [])
            except RuntimeError:
                pass
    for it in items:
        vals = it.get("values") or [{}]
        out[it["name"]] = vals[0].get("value", it.get("total_value", {}).get("value"))
    return out
