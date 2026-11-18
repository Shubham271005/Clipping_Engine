"""YouTube Data API v3 uploader (OAuth installed-app flow, resumable upload, publishAt scheduling)."""
from __future__ import annotations

import os
import random
import time
from pathlib import Path

from ..common import DIRS, ROOT, cfg, get_logger

log = get_logger("youtube")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]
TOKEN = DIRS["secrets"] / "youtube_token.json"


def _client_secrets() -> Path:
    p = Path(os.getenv("YT_CLIENT_SECRETS", "secrets/client_secret.json"))
    return p if p.is_absolute() else ROOT / p


def credentials(interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            log.error("Token refresh failed: %s", e)
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("YouTube not authorized. Run: python main.py auth youtube")
        from google_auth_oauthlib.flow import InstalledAppFlow
        cs = _client_secrets()
        if not cs.exists():
            raise RuntimeError(f"Missing OAuth client file: {cs} (see README > YouTube setup)")
        flow = InstalledAppFlow.from_client_secrets_file(str(cs), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def service(interactive: bool = False):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=credentials(interactive), cache_discovery=False)


def whoami(interactive: bool = False) -> dict:
    r = service(interactive).channels().list(part="snippet,statistics", mine=True).execute()
    items = r.get("items", [])
    if not items:
        raise RuntimeError("Authorized Google account has no YouTube channel")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def upload(path: str, meta: dict, privacy: str = "private", publish_at_utc: str | None = None) -> dict:
    """Upload a video. With publish_at_utc, YouTube flips it public at that time (status must be private)."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    yt = meta["youtube"]
    body = {
        "snippet": {"title": yt["best_title"], "description": yt["description_full"],
                    "tags": yt["tags"], "categoryId": cfg()["publishing"]["youtube"]["category_id"],
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": False},
    }
    if publish_at_utc:
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at_utc.replace("+00:00", "Z")
    media = MediaFileUpload(path, mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = service().videos().insert(part="snippet,status", body=body, media_body=media)
    resp, retry = None, 0
    while resp is None:
        try:
            status, resp = req.next_chunk()
            if status:
                log.info("YouTube upload %d%%", int(status.progress() * 100))
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retry < 8:
                retry += 1
                time.sleep(min(60, 2 ** retry + random.random()))
                continue
            raise
        except (ConnectionError, TimeoutError, OSError):
            if retry < 8:
                retry += 1
                time.sleep(min(60, 2 ** retry + random.random()))
                continue
            raise
    vid = resp["id"]
    log.info("YouTube uploaded id=%s privacy=%s publishAt=%s", vid, resp["status"].get("privacyStatus"),
             resp["status"].get("publishAt"))
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}", "status": resp["status"]}


def stats(video_ids: list[str]) -> dict:
    out = {}
    for i in range(0, len(video_ids), 50):
        r = service().videos().list(part="statistics", id=",".join(video_ids[i:i + 50])).execute()
        for it in r.get("items", []):
            s = it["statistics"]
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                             "comments": int(s.get("commentCount", 0))}
    return out


def analytics(video_id: str, start_date: str, end_date: str) -> dict | None:
    """YouTube Analytics API: retention metrics. Needs the API enabled in your Google Cloud project."""
    from googleapiclient.discovery import build
    try:
        ya = build("youtubeAnalytics", "v2", credentials=credentials(), cache_discovery=False)
        r = ya.reports().query(ids="channel==MINE", startDate=start_date, endDate=end_date,
                               metrics="views,averageViewDuration,averageViewPercentage,shares,likes,comments",
                               filters=f"video=={video_id}").execute()
        if not r.get("rows"):
            return None
        cols = [h["name"] for h in r["columnHeaders"]]
        return dict(zip(cols, r["rows"][0]))
    except Exception as e:
        log.warning("YouTube Analytics unavailable for %s: %s", video_id, str(e)[:160])
        return None
