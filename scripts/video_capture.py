"""
Capture a video as transcript plus evenly-spaced frames, so Claude can read
what was on screen as well as hear what was said. Handles YouTube and public
Instagram reels through one interface.

Why frames matter: on a tutorial the repo name, the exact command and the
config block are usually only ever *shown*. Speech recognition mangles proper
nouns, and platform auto-captions come from the same audio so they repeat the
mistake.

Per source:
  YouTube    yt-dlp for metadata, captions and a low-res video pull. Captions
             are free and better punctuated than speech recognition, so they
             win when they exist; Deepgram covers videos that have none (most
             Shorts). A YouTube video with captions needs no API key at all.
  Instagram  No anonymous download path exists, so the reel is resolved
             through Apify's instagram-scraper actor. Audio always goes to
             Deepgram. Public reels only.

Requires ffmpeg on PATH and yt-dlp. DEEPGRAM_API_KEY for anything without
captions, APIFY_TOKEN for Instagram. Keys are read from the environment first,
then from a .env file in the skill folder, then from a .env in the current
directory. Run `python scripts/setup.py --check` to see what is missing.

Usage:
    python scripts/video_capture.py <url> [--frames-dir DIR] [--max-frames N]

Prints one JSON object to stdout. Progress goes to stderr, so stdout stays
parseable.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from youtube_transcript import get_transcript as youtube_captions  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent

APIFY_ACTOR = "apify~instagram-scraper"
APIFY_ENDPOINT = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
DEEPGRAM_ENDPOINT = (
    "https://api.deepgram.com/v1/listen"
    "?model=nova-3&smart_format=true&punctuate=true&paragraphs=true"
)
# 480p is far more than the 640px-wide frames need, and keeps an hour-long
# tutorial to a sane download.
YTDLP_FORMAT = "bv*[height<=480]+ba/b[height<=480]/worst"

SECONDS_PER_FRAME = 5
# Past this, a YouTube video with captions is transcript-only unless frames are
# asked for explicitly. Frames earn their download on short-form and on tutorials
# where the screen is the content; on a full-length talk they are a big download
# for a sample too sparse to read.
AUTO_TRANSCRIPT_ONLY_SECONDS = 1800  # 30 minutes
# Frames are read as images, so these are context budgets, not performance
# ones. A long tutorial gets more of them because 12 frames across an hour is
# one every five minutes, which is not a reading of the video.
MAX_FRAMES_SHORT = 12
MAX_FRAMES_LONG = 24
LONG_VIDEO_SECONDS = 180

SETUP_HINT = "Run `python scripts/setup.py --check` from the skill folder to see what is missing."


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _read_env_file(path: Path) -> dict:
    env = {}
    if not path.is_file():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_env() -> dict:
    """Environment variables win, then the skill folder's .env, then the cwd's."""
    env = {}
    for path in (Path.cwd() / ".env", SKILL_DIR / ".env"):
        for key, value in _read_env_file(path).items():
            if value:
                env[key] = value
    for key in ("DEEPGRAM_API_KEY", "APIFY_TOKEN"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def detect_source(url: str) -> str:
    if re.search(r"(youtube\.com|youtu\.be)", url):
        return "youtube"
    if re.search(r"instagram\.com/(?:reels?|p)/[\w-]+", url):
        return "instagram"
    raise RuntimeError(
        f"Not a YouTube or Instagram URL: {url}\n"
        "Those are the only two sources this handles."
    )


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------

def _js_runtime_args() -> list:
    """Tell yt-dlp which JavaScript runtime to use, if one is installed.

    YouTube extraction now needs a JS runtime. yt-dlp only enables `deno` by
    default, so on a machine with Node and no Deno it reports "No supported
    JavaScript runtime could be found" and the video download 403s while
    metadata and captions still work. Pointing it at whatever is installed
    fixes that without anyone installing anything new.
    """
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if path:
            return [] if name == "deno" else ["--js-runtimes", name]
    return []


def fetch_youtube(url: str, workdir: Path, frames_mode: str = "auto",
                  have_deepgram: bool = False) -> tuple:
    """Metadata, a local video file, and captions if the video has any.

    frames_mode "never" skips the video download whenever captions exist.
    "auto" does the same for anything past AUTO_TRANSCRIPT_ONLY_SECONDS.
    "always" downloads regardless. Skipping matters: a 2.4-hour talk is a
    228 MB pull that returns a frame every six minutes.
    """
    js_args = _js_runtime_args()
    probe = subprocess.run(
        [sys.executable, "-m", "yt_dlp", *js_args, "-J", "--skip-download", url],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            f"yt-dlp could not read {url}. It may be private, age-restricted, "
            f"or removed.\n{probe.stderr.strip()[:400]}"
        )
    info = json.loads(probe.stdout)

    transcript = None
    try:
        transcript = youtube_captions(url) or None
    except RuntimeError:
        # No caption track. Common on Shorts. Deepgram picks it up later.
        pass
    _log("  captions found" if transcript else "  no caption track, will use Deepgram")
    if not transcript and not have_deepgram:
        raise RuntimeError(
            "This video has no captions, so it needs speech-to-text, and "
            "DEEPGRAM_API_KEY is not set. " + SETUP_HINT
        )

    description = (info.get("description") or "").strip()
    meta = {
        "source": "youtube",
        "permalink": info.get("webpage_url") or url,
        "id": info.get("id"),
        "title": info.get("title"),
        "author": info.get("uploader") or info.get("channel"),
        "caption": description[:2000] + ("..." if len(description) > 2000 else ""),
        "timestamp": info.get("upload_date"),
        "views": info.get("view_count"),
        "reported_duration": float(info.get("duration") or 0),
    }

    reported = meta["reported_duration"]
    if transcript and frames_mode != "always":
        if frames_mode == "never":
            _log("  skipping the video download, transcript only")
            return meta, None, transcript
        if reported > AUTO_TRANSCRIPT_ONLY_SECONDS:
            _log(f"  {reported / 60:.0f} min with captions, so transcript only. "
                 "Pass --frames to force the download and read the screen.")
            return meta, None, transcript

    video = workdir / "video.mp4"
    pull = subprocess.run(
        [
            sys.executable, "-m", "yt_dlp", *js_args, "-f", YTDLP_FORMAT,
            "--merge-output-format", "mp4", "-o", str(video), url,
        ],
        capture_output=True, text=True,
    )
    if not video.exists():
        candidates = [p for p in workdir.iterdir() if p.stem == "video"]
        if candidates:
            video = candidates[0]
        elif transcript:
            # Download failed but the captions are already in hand, so degrade to
            # transcript-only rather than losing the capture entirely. Most common
            # cause is YouTube requiring a JS runtime yt-dlp can't find.
            _log("  video download FAILED, falling back to transcript only (no frames)")
            _log(f"  reason: {pull.stderr.strip().splitlines()[-1][:200] if pull.stderr.strip() else 'unknown'}")
            _log("  installing Node.js usually fixes this. " + SETUP_HINT)
            return meta, None, transcript
        else:
            raise RuntimeError(
                f"yt-dlp downloaded nothing for {url}, and there are no captions to "
                f"fall back on.\n{pull.stderr.strip()[:400]}"
            )

    return meta, video, transcript


# --------------------------------------------------------------------------
# Instagram
# --------------------------------------------------------------------------

def _instagram_permalink(url: str) -> str:
    match = re.search(r"instagram\.com/(?:reels?|p)/([\w-]+)", url)
    return f"https://www.instagram.com/reel/{match.group(1)}/"


def _download_reel_media(permalink: str, apify_url: str, workdir: Path) -> Path:
    """Pull the reel with audio.

    Instagram serves reels as DASH, with video and audio as separate streams.
    The `videoUrl` Apify hands back is a video-only rendition, so downloading
    it directly gives a file with no audio track at all and the transcript
    comes back empty. That is not a silent reel and it is not an Apify fault,
    it is what that field is.

    yt-dlp reads the DASH manifest and merges the streams, so it is the media
    path. Apify is still what resolves the metadata: caption, likes, views and
    the owner, none of which yt-dlp reports reliably for Instagram.

    Falls back to the Apify URL if yt-dlp cannot get it, because a video-only
    capture with frames still beats no capture. It says so when it does,
    rather than letting an empty transcript look like a silent video.
    """
    out = workdir / "reel.mp4"
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
         "--quiet", "--no-warnings", *_js_runtime_args(),
         "-o", str(workdir / "reel.%(ext)s"), permalink],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
        if not has_audio_stream(out):
            _log("  yt-dlp returned no audio track either, this reel may genuinely be silent")
        return out

    _log("  yt-dlp could not fetch the reel, falling back to the Apify video URL")
    _log("  WARNING: that rendition is video only, so expect no transcript")
    pull = urllib.request.Request(apify_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(pull, timeout=180) as response:
        out.write_bytes(response.read())
    if out.stat().st_size == 0:
        raise RuntimeError(
            "Downloaded an empty file. Instagram CDN URLs are signed and "
            "short-lived, so this usually means the Apify result went stale."
        )
    return out


def fetch_instagram(url: str, token: str, workdir: Path) -> tuple:
    """Resolve a public reel through Apify, then pull the media."""
    permalink = _instagram_permalink(url)
    payload = {
        "directUrls": [permalink],
        "resultsType": "reels",
        "resultsLimit": 1,
        "addParentData": False,
    }
    request = urllib.request.Request(
        f"{APIFY_ENDPOINT}?token={token}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            items = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        if exc.code in (401, 403):
            raise RuntimeError(
                f"Apify rejected the token (HTTP {exc.code}). Check APIFY_TOKEN "
                f"against console.apify.com > Settings > API & Integrations.\n{body}"
            ) from exc
        raise RuntimeError(f"Apify returned HTTP {exc.code}.\n{body}") from exc

    if not isinstance(items, list) or not items:
        raise RuntimeError(
            f"Apify returned no results for {url}. The post is most likely "
            "private, deleted, or from an account that blocks scraping. "
            "Only public reels work."
        )
    item = items[0]
    if item.get("error"):
        raise RuntimeError(
            f"Apify could not read {url}: {item.get('error')} "
            f"{item.get('errorDescription', '')}".strip()
        )
    if not item.get("videoUrl"):
        kind = item.get("type") or "unknown"
        raise RuntimeError(
            f"No video in the Apify result for {url} (type: {kind}). "
            "That usually means it's an image post or a carousel, not a reel."
        )

    video = _download_reel_media(permalink, item["videoUrl"], workdir)

    caption = item.get("caption") or ""
    meta = {
        "source": "instagram",
        "permalink": permalink,
        "id": item.get("shortCode"),
        # Reels have no title field. First line of the caption is the closest thing.
        "title": caption.split("\n")[0][:120] or f"Reel by @{item.get('ownerUsername')}",
        "author": item.get("ownerUsername"),
        "author_name": item.get("ownerFullName"),
        "caption": caption,
        "timestamp": item.get("timestamp"),
        "likes": item.get("likesCount"),
        "views": item.get("videoPlayCount"),
    }
    return meta, video, None


# --------------------------------------------------------------------------
# Shared: audio, transcription, frames
# --------------------------------------------------------------------------

def probe_duration(video: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(
            f"Could not determine the video's duration:\n{result.stderr}"
        ) from exc


def has_audio_stream(video: Path) -> bool:
    """Does this file carry an audio track at all?

    A reel with no audio is a real thing, not a download fault: text on screen
    over silence, or music stripped on the way out of Instagram. Worth asking
    before handing it to ffmpeg, because extracting audio from a file with no
    audio stream fails, and on a silent video the frames are the entire
    capture, so losing them is the one unacceptable outcome.
    """
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def extract_audio(video: Path, destination: Path) -> Path:
    """Mono 16kHz mp3, all speech recognition needs, at a fraction of the upload size."""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(destination),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not destination.exists():
        raise RuntimeError(f"ffmpeg failed to extract audio:\n{result.stderr}")
    return destination


def transcribe(audio: Path, key: str) -> str:
    request = urllib.request.Request(
        DEEPGRAM_ENDPOINT,
        data=audio.read_bytes(),
        headers={"Authorization": f"Token {key}", "Content-Type": "audio/mpeg"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        if exc.code in (401, 403):
            raise RuntimeError(
                f"Deepgram rejected the key (HTTP {exc.code}). Check "
                f"DEEPGRAM_API_KEY. {SETUP_HINT}\n{body}"
            ) from exc
        raise RuntimeError(f"Deepgram returned HTTP {exc.code}.\n{body}") from exc

    alternative = data["results"]["channels"][0]["alternatives"][0]
    paragraphs = alternative.get("paragraphs") or {}
    return (paragraphs.get("transcript") or alternative.get("transcript") or "").strip()


def extract_frames(video: Path, duration: float, frames_dir: Path, max_frames: int) -> tuple:
    """Evenly spaced stills, sampled at the midpoint of each slice.

    Returns the frame paths and the real interval between them, which the
    caller needs in order to declare honestly what the sampling could have
    missed.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    count = min(max_frames, max(1, math.ceil(duration / SECONDS_PER_FRAME)))
    interval = duration / count
    paths, stamps = [], []
    for index in range(count):
        timestamp = duration * (index + 0.5) / count
        out = frames_dir / f"frame_{index + 1:02d}.jpg"
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{timestamp:.2f}", "-i", str(video),
                "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(out),
            ],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and out.exists() and out.stat().st_size > 0:
            paths.append(str(out))
            stamps.append(round(timestamp, 1))
        else:
            _log(f"  frame at {timestamp:.1f}s failed, skipping: {result.stderr.strip()[:120]}")
    if not paths:
        raise RuntimeError("ffmpeg produced no usable frames from the video.")
    return paths, round(interval, 1), stamps


def default_max_frames(duration: float) -> int:
    return MAX_FRAMES_LONG if duration > LONG_VIDEO_SECONDS else MAX_FRAMES_SHORT


def _require_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise RuntimeError(f"{' and '.join(missing)} not found on PATH. {SETUP_HINT}")
    probe = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        raise RuntimeError(f"yt-dlp is not installed for this Python. {SETUP_HINT}")


def capture(url: str, frames_dir: Path, max_frames: int = 0, frames_mode: str = "auto") -> dict:
    source = detect_source(url)
    _require_tools()
    env = load_env()
    deepgram_key = env.get("DEEPGRAM_API_KEY")
    if source == "instagram":
        # Check both before Apify is called, so a missing key never costs a scrape.
        if not env.get("APIFY_TOKEN"):
            raise RuntimeError("APIFY_TOKEN is not set, and Instagram needs it. " + SETUP_HINT)
        if not deepgram_key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY is not set, and Instagram reels always need it "
                "for the transcript. " + SETUP_HINT
            )

    with tempfile.TemporaryDirectory(prefix="capture_") as tmp:
        workdir = Path(tmp)
        _log(f"Fetching {source}: {url}")
        if source == "youtube":
            meta, video, transcript = fetch_youtube(
                url, workdir, frames_mode=frames_mode, have_deepgram=bool(deepgram_key))
        else:
            meta, video, transcript = fetch_instagram(url, env["APIFY_TOKEN"], workdir)

        if video is None:
            # Transcript-only YouTube pull. Nothing was downloaded.
            duration = meta.pop("reported_duration", 0.0)
            _log(f"  @{meta.get('author')} | {duration:.0f}s | transcript only, no frames")
            return {
                **meta,
                "duration_seconds": round(duration, 1),
                "transcript": transcript,
                "transcript_source": "captions",
                "frames_dir": None,
                "frame_interval_seconds": None,
                "frame_timestamps": [],
                "frames": [],
            }

        meta.pop("reported_duration", None)
        duration = probe_duration(video)
        _log(f"  @{meta.get('author')} | {duration:.0f}s | "
             f"{video.stat().st_size / 1_000_000:.1f} MB")

        transcript_source = "captions"
        if not transcript:
            if not has_audio_stream(video):
                # Not a failure. Carry on to the frames, which are now the
                # entire capture, and say so loudly in the output so nobody
                # later reads an empty transcript as a broken run.
                _log("  NO AUDIO TRACK, the frames are the whole story on this one")
                transcript = ""
                transcript_source = "none (silent video)"
            else:
                _log("Transcribing with Deepgram...")
                transcript = transcribe(extract_audio(video, workdir / "audio.mp3"),
                                        deepgram_key)
                transcript_source = "deepgram"
                if not transcript:
                    _log("  no speech detected, the frames are the whole story on this one")

        if frames_mode == "never":
            # Instagram still needs the download for audio, but nobody asked for frames.
            _log("  skipping frame extraction, transcript only")
            frame_paths, interval, stamps = [], None, []
        else:
            limit = max_frames or default_max_frames(duration)
            _log(f"Extracting up to {limit} frames to {frames_dir}...")
            frame_paths, interval, stamps = extract_frames(video, duration, frames_dir, limit)
            _log(f"  {len(frame_paths)} frames, one every {interval}s")
            if interval > 120:
                _log(f"  WARNING: one frame per {interval / 60:.0f} minutes is too sparse "
                     "to read the screen. Raise --max-frames or treat the transcript as "
                     "the payload.")

    return {
        **meta,
        "duration_seconds": round(duration, 1),
        "transcript": transcript,
        "transcript_source": transcript_source,
        "frames_dir": str(frames_dir),
        "frame_interval_seconds": interval,
        "frame_timestamps": stamps,
        "frames": frame_paths,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a YouTube or Instagram video.")
    parser.add_argument("url", help="YouTube or Instagram reel URL")
    parser.add_argument(
        "--frames-dir",
        help="Where to write the frames. Defaults to a new temp directory.",
    )
    parser.add_argument(
        "--max-frames", type=int, default=0,
        help=f"Override the cap ({MAX_FRAMES_SHORT} under "
             f"{LONG_VIDEO_SECONDS}s, {MAX_FRAMES_LONG} over). Raise it for a "
             "screen-heavy tutorial where the detail is in the code.",
    )
    parser.add_argument(
        "--no-frames", action="store_true",
        help="Transcript only. On YouTube with captions this skips the video "
             "download entirely, which is the difference between instant and a "
             "228 MB pull on a multi-hour talk.",
    )
    parser.add_argument(
        "--frames", action="store_true",
        help=f"Force frames even on a long video. Without this, YouTube videos "
             f"over {AUTO_TRANSCRIPT_ONLY_SECONDS // 60} min with captions are "
             "transcript-only. Instagram reels always get frames.",
    )
    args = parser.parse_args()
    if args.frames and args.no_frames:
        parser.error("--frames and --no-frames contradict each other.")

    # Captions carry emoji constantly and Windows pipes default to cp1252, so
    # redirecting this to a file would otherwise die on the first one after
    # everything was already downloaded.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    mode = "never" if args.no_frames else ("always" if args.frames else "auto")

    frames_dir = (
        Path(args.frames_dir)
        if args.frames_dir
        else Path(tempfile.mkdtemp(prefix="capture_frames_"))
    )
    try:
        print(json.dumps(
            capture(args.url, frames_dir, args.max_frames, frames_mode=mode),
            indent=2, ensure_ascii=False))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
