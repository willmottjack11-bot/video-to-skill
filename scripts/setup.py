"""
Setup helper for the video-to-skill skill. Checks everything the capture
script needs and helps fix what is missing.

Two ways to use it:

  A person in a terminal (interactive, keys typed with hidden input):
      python scripts/setup.py

  Claude, from inside a chat (machine-readable report plus fix commands):
      python scripts/setup.py --check
      python scripts/setup.py --install-ytdlp
      python scripts/setup.py --save-key DEEPGRAM_API_KEY <value>
      python scripts/setup.py --save-key APIFY_TOKEN <value>
      python scripts/setup.py --set-captures-dir <path>

Keys are saved to a .env file in the skill folder. That file is listed in
.gitignore, so it never ends up in a commit if the folder is ever pushed.
"""
import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = SKILL_DIR / ".env"
DEFAULT_CAPTURES_DIR = Path.home() / "video-captures"
SAVEABLE = ("DEEPGRAM_API_KEY", "APIFY_TOKEN", "CAPTURES_DIR")

DEEPGRAM_SIGNUP = "https://console.deepgram.com/signup"
APIFY_SIGNUP = "https://console.apify.com/sign-up"
APIFY_TOKEN_PAGE = "https://console.apify.com/settings/integrations"

OS = platform.system()  # "Windows", "Darwin" or "Linux"
SELF = f'"{sys.executable}" "{Path(__file__).resolve()}"'


# --------------------------------------------------------------------------
# .env handling
# --------------------------------------------------------------------------

def read_env() -> dict:
    env = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in SAVEABLE:
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def write_env_value(name: str, value: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.is_file() else []
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith(f"{name}="):
            out.append(f"{name}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    if OS != "Windows":
        os.chmod(ENV_FILE, 0o600)


def captures_dir(env: dict) -> Path:
    raw = env.get("CAPTURES_DIR")
    return Path(raw).expanduser() if raw else DEFAULT_CAPTURES_DIR


# --------------------------------------------------------------------------
# Key verification. One cheap, read-only call each.
# --------------------------------------------------------------------------

def _get(url: str, headers: dict = None) -> int:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError):
        return 0


def verify_key(name: str, value: str) -> tuple:
    """Returns (status, message). status is "valid", "invalid" or "unknown"."""
    if name == "DEEPGRAM_API_KEY":
        code = _get("https://api.deepgram.com/v1/projects",
                    {"Authorization": f"Token {value}"})
    elif name == "APIFY_TOKEN":
        code = _get(f"https://api.apify.com/v2/users/me?token={value}")
    else:
        return "unknown", "not a key"
    if code == 200:
        return "valid", "accepted by the service"
    if code in (401, 403):
        return "invalid", f"rejected by the service (HTTP {code}), check it was copied in full"
    if code == 0:
        return "unknown", "could not reach the service to check it, saved anyway"
    return "unknown", f"service answered HTTP {code}, saved anyway"


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _fix(windows: str, mac: str, linux: str) -> str:
    return {"Windows": windows, "Darwin": mac}.get(OS, linux)


RESTART_NOTE = (
    " Then fully close and reopen Claude Code (or your terminal) so it picks up "
    "the new PATH."
)


def ytdlp_version() -> str:
    result = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def run_checks(verify: bool = True) -> dict:
    env = read_env()
    checks = []

    py_ok = sys.version_info >= (3, 9)
    checks.append({
        "id": "python", "label": "Python 3.9 or newer", "level": "required",
        "ok": py_ok, "detail": f"{platform.python_version()} at {sys.executable}",
        "fix": None if py_ok else "Install a newer Python from https://www.python.org/downloads/",
        "claude_can_fix": False,
    })

    version = ytdlp_version()
    checks.append({
        "id": "yt-dlp", "label": "yt-dlp (downloads the video and captions)",
        "level": "required", "ok": bool(version),
        "detail": f"version {version}" if version else "not installed for this Python",
        "fix": None if version else f'"{sys.executable}" -m pip install -U yt-dlp',
        "claude_can_fix": not version,
        "claude_fix_command": None if version else f"{SELF} --install-ytdlp",
    })

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    ff_ok = bool(ffmpeg and ffprobe)
    checks.append({
        "id": "ffmpeg", "label": "ffmpeg and ffprobe (cut frames and audio)",
        "level": "required", "ok": ff_ok,
        "detail": ffmpeg or "not found on PATH",
        "fix": None if ff_ok else _fix(
            "winget install --id Gyan.FFmpeg -e" + RESTART_NOTE,
            "brew install ffmpeg   (no Homebrew? get it at https://brew.sh first)",
            "sudo apt install ffmpeg   (or your distro's package manager)",
        ),
        "claude_can_fix": False,
    })

    runtime = next((name for name in ("deno", "node", "bun") if shutil.which(name)), None)
    checks.append({
        "id": "js-runtime", "label": "Node.js (lets yt-dlp download YouTube video for frames)",
        "level": "recommended", "ok": bool(runtime),
        "detail": f"{runtime} found" if runtime else (
            "none found. YouTube captions still work, but frames may fail with HTTP 403"),
        "fix": None if runtime else _fix(
            "winget install --id OpenJS.NodeJS.LTS -e" + RESTART_NOTE,
            "brew install node",
            "sudo apt install nodejs",
        ),
        "claude_can_fix": False,
    })

    for name, label, level, signup, why in (
        ("DEEPGRAM_API_KEY", "Deepgram API key (speech to text)", "required",
         DEEPGRAM_SIGNUP,
         "Needed for every Instagram reel and any YouTube video without captions. "
         "YouTube videos with captions work without it. Sign up, then create an "
         "API key in the console. New accounts come with free credit."),
        ("APIFY_TOKEN", "Apify API token (Instagram only)", "optional",
         APIFY_SIGNUP,
         f"Only needed for Instagram reels. Sign up, then copy the token from "
         f"{APIFY_TOKEN_PAGE}. The free plan's monthly credit covers a lot of reels."),
    ):
        value = env.get(name)
        status, message = ("missing", "not set")
        if value:
            status, message = verify_key(name, value) if verify else ("present", "set, not verified")
        ok = status in ("valid", "present", "unknown")
        checks.append({
            "id": name, "label": label, "level": level, "ok": ok,
            "detail": message, "status": status,
            "fix": None if ok else f"{why} Sign up: {signup}",
            "claude_can_fix": not ok,
            "claude_fix_command": None if ok else (
                f"{SELF} --save-key {name} <the key the user pasted>"),
        })

    required_ok = all(c["ok"] for c in checks if c["level"] == "required")
    youtube_ok = all(c["ok"] for c in checks if c["id"] in ("python", "yt-dlp", "ffmpeg"))
    instagram_ok = required_ok and next(c for c in checks if c["id"] == "APIFY_TOKEN")["ok"]
    return {
        "ready": required_ok,
        "can_do": {
            "youtube_with_captions": youtube_ok,
            "youtube_without_captions": required_ok,
            "instagram_reels": instagram_ok,
        },
        "os": OS,
        "skill_dir": str(SKILL_DIR),
        "env_file": str(ENV_FILE),
        "captures_dir": str(captures_dir(env)),
        "capture_command": f'"{sys.executable}" "{SKILL_DIR / "scripts" / "video_capture.py"}" <url>',
        "checks": checks,
    }


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def install_ytdlp() -> bool:
    print("Installing yt-dlp...", file=sys.stderr)
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
    ok = result.returncode == 0 and bool(ytdlp_version())
    print("yt-dlp installed." if ok else "yt-dlp install failed, see the output above.")
    return ok


def save_key(name: str, value: str) -> bool:
    value = value.strip().strip('"').strip("'")
    if name not in ("DEEPGRAM_API_KEY", "APIFY_TOKEN"):
        print(f"Unknown key name {name}. Use DEEPGRAM_API_KEY or APIFY_TOKEN.")
        return False
    if not value:
        print("Empty value, nothing saved.")
        return False
    status, message = verify_key(name, value)
    if status == "invalid":
        print(f"{name} NOT saved: {message}.")
        return False
    write_env_value(name, value)
    print(f"{name} saved to {ENV_FILE} ({message}).")
    return True


def set_captures_dir(path: str) -> None:
    target = Path(path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    write_env_value("CAPTURES_DIR", str(target))
    print(f"Captures will be saved to {target}")


# --------------------------------------------------------------------------
# Interactive mode, for a person in a terminal
# --------------------------------------------------------------------------

def _print_report(report: dict) -> None:
    print()
    print("video-to-skill setup check")
    print("=" * 40)
    for check in report["checks"]:
        mark = "[ok]     " if check["ok"] else (
            "[missing]" if check["level"] != "optional" else "[skipped]")
        print(f"{mark} {check['label']}")
        print(f"          {check['detail']}")
    print()
    print(f"Captures folder: {report['captures_dir']}")
    print()


def _ask(prompt: str) -> bool:
    answer = input(f"{prompt} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


def interactive() -> None:
    report = run_checks()
    _print_report(report)

    by_id = {c["id"]: c for c in report["checks"]}

    if not by_id["yt-dlp"]["ok"] and _ask("yt-dlp is missing. Install it now?"):
        install_ytdlp()

    for tool in ("ffmpeg", "js-runtime"):
        if not by_id[tool]["ok"]:
            print(f"\n{by_id[tool]['label']} is missing. Install it with:")
            print(f"    {by_id[tool]['fix']}")

    for name, signup in (("DEEPGRAM_API_KEY", DEEPGRAM_SIGNUP), ("APIFY_TOKEN", APIFY_SIGNUP)):
        if by_id[name]["ok"]:
            continue
        print(f"\n{by_id[name]['label']}")
        print(f"  {by_id[name]['fix']}")
        while True:
            value = getpass.getpass(f"  Paste your {name} (hidden, press Enter to skip): ")
            if not value.strip():
                print("  Skipped.")
                break
            if save_key(name, value):
                break

    print("\nFinal state:")
    report = run_checks()
    _print_report(report)
    if report["ready"]:
        print("All set. In Claude Code, say: capture this <YouTube or Instagram link>")
    elif report["can_do"]["youtube_with_captions"]:
        print("YouTube videos with captions will work now. Add the rest later by "
              "running this again.")
    else:
        print("Not ready yet. Install what is marked [missing] and run this again.")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Check and set up video-to-skill.")
    parser.add_argument("--check", action="store_true",
                        help="Print a JSON report of what is installed and what is missing.")
    parser.add_argument("--no-verify", action="store_true",
                        help="With --check, skip the live test of saved keys.")
    parser.add_argument("--install-ytdlp", action="store_true",
                        help="pip install yt-dlp for this Python.")
    parser.add_argument("--save-key", nargs=2, metavar=("NAME", "VALUE"),
                        help="Verify a key and save it to the skill's .env.")
    parser.add_argument("--set-captures-dir", metavar="PATH",
                        help="Where capture files are saved (default ~/video-captures).")
    args = parser.parse_args()

    if args.check:
        print(json.dumps(run_checks(verify=not args.no_verify), indent=2))
    elif args.install_ytdlp:
        sys.exit(0 if install_ytdlp() else 1)
    elif args.save_key:
        sys.exit(0 if save_key(*args.save_key) else 1)
    elif args.set_captures_dir:
        set_captures_dir(args.set_captures_dir)
    elif sys.stdin.isatty() and sys.stdout.isatty():
        # Both ends checked: Windows reports a NUL stdin as a terminal, and the
        # hidden key prompt would then wait forever on a console nobody is at.
        interactive()
    else:
        # Not a terminal, so there is nobody to answer prompts. Report instead.
        print(json.dumps(run_checks(), indent=2))


if __name__ == "__main__":
    main()
