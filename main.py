"""AI Clipping Engine - game-agnostic, profile-driven.

  python main.py --profile gta6 --url "VOD_URL_OR_FILE" --streamer NAME --rights permission_granted \
                 --commercial --auth-ref "email 2026-11-19 from NAME"
  python main.py --profile valorant --url "VOD_URL"

Other commands:  profiles | status | rights | auth | schedule | publish-due | test-upload |
                 analytics | narrate          (python main.py <command> -h for details)
"""
from __future__ import annotations

import argparse
import json
import sys

COMMANDS = {"profiles", "status", "rights", "auth", "schedule", "publish-due", "test-upload",
            "analytics", "narrate", "rerender", "render-candidate", "approve", "reject"}


def _process(argv):
    p = argparse.ArgumentParser(description="Process one VOD/stream into ranked, rendered, scheduled clips")
    p.add_argument("--profile", default="gta6", help="content profile from profiles/ (gta6, valorant, ...)")
    p.add_argument("--url", required=True, help="authorized VOD URL or local video file")
    p.add_argument("--streamer", help="original creator name (credited in metadata)")
    p.add_argument("--game", help="override the profile's game label (e.g. 'GTA 6 trailer')")
    p.add_argument("--rights", choices=["unknown", "owned", "permission_granted", "denied"])
    p.add_argument("--commercial", action="store_true", help="explicit commercial-use permission exists")
    p.add_argument("--auth-ref", help="where the written permission is stored (email/contract/DM link)")
    p.add_argument("--authorized-on", help="YYYY-MM-DD the permission was granted")
    p.add_argument("--restrictions", help="e.g. 'no-instagram', 'no-publish', free text")
    p.add_argument("--max-clips", type=int)
    p.add_argument("--no-llm", action="store_true", help="signal-only scoring (no Claude CLI)")
    p.add_argument("--min-score", type=float, help="override the minimum virality score for this run")
    p.add_argument("--no-schedule", action="store_true")
    p.add_argument("--force-top", type=int, default=0,
                   help="also render the N best REJECTED moments as previews (never scheduled)")
    a = p.parse_args(argv)
    from src.common import cfg, set_profile
    set_profile(a.profile)
    if a.no_llm:
        cfg()["review"]["use_llm"] = False
    if a.min_score is not None:
        cfg()["review"]["min_virality"] = a.min_score
    from src.pipeline import process
    s = process(a.url, a.streamer, a.game, a.rights, a.commercial, a.auth_ref, a.authorized_on,
                a.restrictions, a.max_clips, not a.no_schedule, a.force_top)
    print(json.dumps(s, indent=2))


def _commands(argv):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("profiles")
    sub.add_parser("status")
    r = sub.add_parser("rights")
    r.add_argument("action", choices=["set", "list"])
    r.add_argument("source_id", nargs="?")
    r.add_argument("--status", choices=["unknown", "owned", "permission_granted", "denied"])
    r.add_argument("--commercial", action="store_true")
    r.add_argument("--ref")
    r.add_argument("--authorized-on")
    r.add_argument("--restrictions")
    r.add_argument("--streamer")
    au = sub.add_parser("auth")
    au.add_argument("platform", choices=["youtube", "instagram"])
    sub.add_parser("schedule")
    sub.add_parser("publish-due")
    t = sub.add_parser("test-upload")
    t.add_argument("platform", choices=["youtube", "instagram"])
    t.add_argument("--clip", required=True)
    an = sub.add_parser("analytics")
    an.add_argument("action", choices=["fetch", "report"])
    n = sub.add_parser("narrate")
    n.add_argument("clip_id")
    n.add_argument("audio_file")
    n.add_argument("--at", type=float, default=0.0, help="seconds into the clip where narration starts")
    rr = sub.add_parser("rerender", help="re-render a clip with a new trim / hook / caption fixes")
    rr.add_argument("clip_id")
    rr.add_argument("--start", type=float, help="new start (seconds in the source video)")
    rr.add_argument("--end", type=float)
    rr.add_argument("--hook", help="on-screen hook text ('' removes it)")
    rr.add_argument("--corrections", help='JSON object of caption fixes, e.g. {"Kenu": "Keanu"}')
    rc = sub.add_parser("render-candidate", help="turn any detected moment (even AI-rejected) into a clip")
    rc.add_argument("candidate_id", type=int)
    for name in ("approve", "reject"):
        ap = sub.add_parser(name)
        ap.add_argument("clip_ids", nargs="+")
    a = p.parse_args(argv)

    from src import db
    if a.cmd == "profiles":
        from src.common import list_profiles, load_profile
        for name in list_profiles():
            pr = load_profile(name)
            print(f"{name:12s} {pr['name']}  ({len(pr['categories'])} categories)")
    elif a.cmd == "status":
        for s in db.rows("SELECT id,profile,streamer,title,rights_status,commercial_use FROM sources"):
            n = db.one("SELECT COUNT(*) n FROM clips WHERE source_id=?", (s["id"],))["n"]
            print(f"{s['id']} [{s['profile']}] {s['streamer']} | rights={s['rights_status']} "
                  f"commercial={bool(s['commercial_use'])} | clips={n} | {s['title']}")
        for r in db.rows("SELECT platform,status,COUNT(*) n FROM publications GROUP BY platform,status"):
            print(f"  {r['platform']:10s} {r['status']:10s} {r['n']}")
        for r in db.rows("SELECT clip_id,platform,status,scheduled_for,url,last_error FROM publications "
                         "ORDER BY scheduled_for LIMIT 30"):
            print(f"  {r['scheduled_for']} {r['platform']:9s} {r['status']:9s} {r['clip_id']} {r['url'] or ''} "
                  f"{(r['last_error'] or '')[:80]}")
    elif a.cmd == "rights":
        from src.ingestion import rights
        if a.action == "list":
            for s in db.rows("SELECT * FROM sources"):
                print(json.dumps({k: s[k] for k in ("id", "streamer", "url", "rights_status", "commercial_use",
                                                    "authorized_on", "authorization_ref", "restrictions")}))
        else:
            print(json.dumps(rights.set_rights(a.source_id, a.status, a.commercial, a.authorized_on, a.ref,
                                               a.restrictions, a.streamer), indent=2, default=str))
    elif a.cmd == "auth":
        if a.platform == "youtube":
            from src.publishing import youtube
            print("Authorized YouTube channel:", youtube.whoami(interactive=True))
        else:
            from src.publishing import instagram
            print("Authorized Instagram account:", instagram.authorize())
    elif a.cmd == "schedule":
        from src.publishing.scheduler import schedule_all
        print(json.dumps(schedule_all(), indent=2))
    elif a.cmd == "publish-due":
        from src.publishing.scheduler import publish_due
        print(json.dumps(publish_due(), indent=2))
    elif a.cmd == "test-upload":
        clip = db.one("SELECT * FROM clips WHERE id=?", (a.clip,))
        if not clip:
            sys.exit(f"unknown clip {a.clip}")
        meta = json.loads(clip["metadata_json"])
        if a.platform == "youtube":
            from src.publishing import youtube
            r = youtube.upload(clip["path"], meta, privacy="private")
            print("PRIVATE test upload OK:", r["url"], "(visible only to you; delete it in YouTube Studio)")
        else:
            from src.publishing import instagram
            print("Account:", instagram.authorize())
            cid = instagram.create_and_upload(clip["path"], meta["instagram"]["caption_full"])
            print("Container status:", instagram.wait_ready(cid),
                  f"-> container {cid} uploaded and processed but NOT published (expires in 24h).")
            print("Publishing quota:", instagram.publishing_quota())
    elif a.cmd == "analytics":
        from src.analytics import collect
        if a.action == "fetch":
            print(json.dumps(collect.fetch(), indent=2))
        print(collect.learn_and_report())
    elif a.cmd == "rerender":
        from src.editing.manual import rerender_clip
        corr = json.loads(a.corrections) if a.corrections else None
        print(json.dumps(rerender_clip(a.clip_id, a.start, a.end, a.hook, corr), indent=2, default=str))
    elif a.cmd == "render-candidate":
        from src.editing.manual import render_candidate
        print(json.dumps(render_candidate(a.candidate_id), indent=2, default=str))
    elif a.cmd in ("approve", "reject"):
        status = "approved" if a.cmd == "approve" else "rejected"
        for cid in a.clip_ids:
            db.execute("UPDATE clips SET review_status=?, updated_at=? WHERE id=?", (status, db.now_iso(), cid))
            if status == "rejected":
                db.execute("DELETE FROM publications WHERE clip_id=? AND status='scheduled'", (cid,))
        print(f"{len(a.clip_ids)} clip(s) {status}")
        if status == "approved":
            from src.publishing.scheduler import schedule_all
            print(json.dumps(schedule_all(), indent=2))
    elif a.cmd == "narrate":
        from src.editing.narration import add_narration
        print(add_narration(a.clip_id, a.audio_file, a.at))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; titles contain emoji
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    args = sys.argv[1:]
    if args and args[0] in COMMANDS:
        _commands(args)
    else:
        _process(args)
