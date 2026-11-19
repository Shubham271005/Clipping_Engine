"""Upload the owner's own 10-second test video (no third-party content) as PRIVATE, using the same
upload code the publisher uses. Handy for API audit screencasts.

    .venv\\Scripts\\python scripts\\upload_test.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.publishing import youtube  # noqa: E402

video = ROOT / "work" / "yt_test.mp4"
if not video.exists():
    video.parent.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x1a1a2e:s=1080x1920:d=10,drawtext=fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
                    "text='Clip Studio':fontcolor=white:fontsize=96:x=(w-text_w)/2:y=(h/2)-120",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=10:sample_rate=48000",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)

print("Channel:", youtube.whoami()["title"])
meta = {"youtube": {"best_title": "Clip Studio upload test (private)",
                    "description_full": "Automated upload test from Clip Studio (owner's own test video).",
                    "tags": ["test"]}}
r = youtube.upload(str(video), meta, privacy="private")
print("Uploaded:", r["url"], "| privacy:", r["status"].get("privacyStatus"))
print("Open YouTube Studio > Content to see it.")
