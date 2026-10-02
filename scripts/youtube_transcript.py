"""
Fetch a YouTube video's transcript as clean plain text, via yt-dlp.
YouTube's caption endpoint requires a signed proof-of-origin token that plain
HTTP requests can't produce, so this delegates to a tool that's actively
maintained to keep up with that.

Usage:
    python scripts/youtube_transcript.py <video_id_or_url>

Downloads auto-generated (or manual, if available) English subtitles to a
temp .vtt file, cleans up the rolling-caption duplication YouTube's format
uses, and prints the plain-text result.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _video_id(video_id_or_url: str) -> str:
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", video_id_or_url)
    return match.group(1) if match else video_id_or_url


def _clean_vtt(vtt_text: str) -> str:
    # Strip header, timing lines, and inline <...> word-timing tags.
    lines = []
    for line in vtt_text.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:")):
            continue
        if "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line:
            lines.append(line)

    # YouTube auto-captions "roll up": each cue often repeats the previous
    # cue's text plus new words. Drop any line that's a prefix of the next
    # one, keeping only each group's final, fullest state.
    deduped = []
    for i, line in enumerate(lines):
        next_line = lines[i + 1] if i + 1 < len(lines) else None
        if next_line and next_line.startswith(line):
            continue
        if deduped and line == deduped[-1]:
            continue
        deduped.append(line)
    return "\n".join(deduped)


def get_transcript(video_id_or_url: str) -> str:
    video_id = _video_id(video_id_or_url)
    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / "%(id)s.%(ext)s")
        result = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "--write-auto-sub", "--write-sub", "--sub-lang", "en",
                "--skip-download", "--sub-format", "vtt",
                "-o", out_template,
                f"https://www.youtube.com/watch?v={video_id}",
            ],
            capture_output=True, text=True,
        )
        vtt_files = list(Path(tmp).glob("*.vtt"))
        if not vtt_files:
            raise RuntimeError(
                f"No subtitles found for {video_id} (video may have captions disabled).\n{result.stderr}"
            )
        return _clean_vtt(vtt_files[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python youtube_transcript.py <video_id_or_url>", file=sys.stderr)
        sys.exit(1)
    print(get_transcript(sys.argv[1]))
