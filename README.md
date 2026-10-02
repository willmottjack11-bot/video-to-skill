# video-to-skill

A Claude Code skill that turns a YouTube video or an Instagram reel into something you can use later: the transcript, a summary of what's useful, and, if the video teaches a repeatable process, a Claude Code skill built from it.

It reads the **screen** as well as the audio. Tutorials show the repo name, the command and the config on screen and rarely say them out loud, and speech-to-text mishears names. So it pulls frames from the video and checks them against the transcript.

## Install

**Easiest:** open Claude Code and paste this:

> Install the Claude Code skill from https://github.com/willmottjack11-bot/video-to-skill into my personal skills folder (~/.claude/skills/video-to-skill), then run its setup check.

**Or do it yourself** in a terminal:

```bash
# macOS / Linux
git clone https://github.com/willmottjack11-bot/video-to-skill.git ~/.claude/skills/video-to-skill
```

```powershell
# Windows (PowerShell)
git clone https://github.com/willmottjack11-bot/video-to-skill.git "$HOME\.claude\skills\video-to-skill"
```

Then start a new Claude Code session.

## Use

Just paste a link into Claude Code:

> capture this https://www.youtube.com/watch?v=...

> what's useful in this reel https://www.instagram.com/reel/...

**The first time you run it, it checks your setup and walks you through anything missing.** You don't need to set anything up before you start.

Captures are saved to `~/video-captures/` with an `index.md` listing everything you've captured.

## What it needs

| Thing | Why | Cost |
|---|---|---|
| Python 3.9+ | runs the scripts | free |
| yt-dlp | downloads videos and captions | free, the setup can install it for you |
| ffmpeg | cuts frames and audio | free |
| Node.js (recommended) | lets yt-dlp download YouTube video for frames | free |
| [Deepgram](https://console.deepgram.com/signup) API key | speech-to-text for reels and videos without captions | free credit on signup, then a fraction of a cent per minute |
| [Apify](https://console.apify.com/sign-up) token | Instagram reels only | free monthly credit, a fraction of a cent per reel |

**YouTube videos with captions work with no keys at all.**

## Setup on your own

If you'd rather set it up before using it, run this in a terminal. It checks everything, offers to install yt-dlp, and asks for your keys with hidden input:

```bash
python ~/.claude/skills/video-to-skill/scripts/setup.py
```

(`python3` on macOS and Linux.)

## Your keys

Keys are saved to a `.env` file inside the skill folder, on your machine only. That file is in `.gitignore`, so it won't get committed if you ever push the folder anywhere. Nothing is sent anywhere except the service each key belongs to.

## Limits

- Public Instagram reels only. Private accounts, image posts and carousels won't work.
- YouTube videos over 30 minutes with captions are transcript-only by default (a frame every few minutes isn't worth the download). Ask for frames if you want them.
- It never builds a skill on its own. It asks first.

## Make it better

It works as is. These are upgrades you can add when you want to. Paste any of them into Claude Code and it'll do the work.

**Test it on your own videos**
> Run video-to-skill on one YouTube video with captions, one YouTube Short and one Instagram reel. Tell me what broke or looked wrong.

**Add automated tests**, so you know it still works after you change something
> Write pytest tests for the scripts in ~/.claude/skills/video-to-skill/scripts. Cover URL detection, the caption cleanup and the setup check. Run them and fix anything that fails.

**Test when it triggers.** If you have Anthropic's skill-creator plugin:
> Use skill-creator to run evals on video-to-skill. Check it fires on the way I actually ask for it, and stays quiet on things that aren't about videos.

**Add TikTok**
> Add TikTok support to video-to-skill. yt-dlp already downloads public TikToks, so route them like YouTube Shorts.

**Save captures into your notes app**
> Change video-to-skill so captures save into my Obsidian vault at <path> instead of ~/video-captures.

**Capture a batch**
> Capture every link in this list and give me one combined summary of what's worth building.

(Each Instagram reel costs a fraction of a cent, so check the list length before a big batch.)
