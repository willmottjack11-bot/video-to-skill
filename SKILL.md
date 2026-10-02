---
name: video-to-skill
description: "Use when the user pastes a YouTube link or a public Instagram reel link, or a transcript they copied themselves, and wants it captured or turned into a skill. Triggers on phrases like grab the transcript from this video, capture this for later, what's useful in this video, turn this video into a skill, or summarise this reel. Captures the transcript plus frames of what was on screen, saves a summary to a captures folder, and offers to build a Claude Code skill from it if the video teaches a repeatable process. Also use when the user asks to set up or check the video-to-skill skill."
argument-hint: "[YouTube or Instagram reel URL, or a pasted transcript]"
---

## What this skill does

Turns a video into something you can actually use later: a clean transcript, a short summary of what's useful, and, when the video teaches a repeatable process, a Claude Code skill built from it.

It reads the screen as well as the audio. On a tutorial the repo name, the exact command and the config block are usually only ever *shown*, and speech-to-text confidently mishears proper nouns. A transcript-only capture gives you a plausible wrong answer. Reading frames fixes that.

**Sources:** YouTube and public Instagram reels. Anything else (TikTok, LinkedIn, a bare video file) gets a plain "this skill doesn't cover that" and stops.

**Scripts live in this skill's base directory** (Claude Code shows it when the skill loads, normally `~/.claude/skills/video-to-skill`). Always call them by full path, quoted, because the user's working directory is usually somewhere else. Use `python` on Windows and `python3` on macOS and Linux.

## Step 0: check setup (every run, takes a second)

```
python "<skill dir>/scripts/setup.py" --check --no-verify
```

It prints JSON: `ready`, `can_do` (which sources work right now), `captures_dir`, `capture_command`, and a `checks` list where each item has `ok`, `level`, `detail`, `fix` and sometimes a `claude_fix_command`.

- **`ready` is true:** go straight to Step 1. Don't mention setup.
- **Anything missing:** this is probably the user's first run. Walk them through it, one item at a time, in plain words:
  - Say what's missing and what it unlocks. Use `can_do`: if YouTube with captions already works, say so, and offer to carry on with their video now if that's what they gave you.
  - **yt-dlp**: offer to install it yourself with its `claude_fix_command`. Ask first.
  - **ffmpeg** and **Node.js**: system installs. Show the `fix` command for their OS and offer to run it for them. Ask first. On Windows, after a `winget` install they must fully close and reopen Claude Code before the new PATH is visible, so tell them that and stop there.
  - **API keys** (Deepgram always for Instagram and for YouTube without captions; Apify only for Instagram): give them the signup link from `fix`, then offer two ways to hand it over:
    1. Most private: they run `python "<skill dir>/scripts/setup.py"` in their own terminal. It asks for keys with hidden input and checks them.
    2. Quickest: paste it in this chat, and you save it with `python "<skill dir>/scripts/setup.py" --save-key NAME VALUE`, which tests the key before saving. Mention that a pasted key stays in the chat history.

    Never echo a key back, never write one anywhere except through `--save-key`, never put one in a capture file.
  - When done, re-run `--check` **without** `--no-verify` so the saved keys get tested live, and confirm what now works.

If the user just asks to "set up video-to-skill" with no link, run Step 0 and stop when everything is green.

## Step 1: capture

**Two input paths:**

- **A YouTube or Instagram URL:** run the `capture_command` from Step 0 with the URL.
- **A pasted transcript** (copied from YouTube's own transcript panel): skip to Step 3. Ask for the title or URL if they didn't include one, so the saved file records the source. Note what they give up: no frames, so anything only shown on screen is lost. For a screen-heavy tutorial, offer to run the capture properly instead.

```
python "<skill dir>/scripts/video_capture.py" <url>
```

It prints one JSON object: `source`, `title`, `author`, `caption`, `duration_seconds`, `transcript`, `transcript_source`, `frames`, `frame_timestamps`, `frame_interval_seconds`.

- **YouTube:** captions win when they exist (free, well punctuated). Deepgram covers videos with none (most Shorts).
- **Instagram:** resolved through Apify, transcribed by Deepgram. Public reels only.
- **Frames** are evenly spaced: up to 12 under 3 minutes, 24 over. YouTube videos over 30 minutes with captions are transcript-only automatically, because the download is big and a frame every few minutes isn't a reading of the screen. `--frames` forces frames anyway, `--no-frames` skips them.

It fails loudly and specifically (private account, image post not a reel, age-restricted, missing key). **Relay the actual cause** rather than saying it didn't work. If the error says to run setup, go back to Step 0.

**Cost:** Instagram costs a fraction of a cent per reel on Apify, and Deepgram charges a fraction of a cent per minute of audio. Both have free credit. Small, but not zero, so never loop this over a whole profile or channel without asking.

## Step 2: read the frames and reconcile them with the transcript

This is where the real content often is: tool names, commands, code, prices and diagrams that the presenter never says out loud.

**First classify the video by reading about 5 frames spread across it**, then decide how many more to read:

| Type | Frames worth | Why |
|---|---|---|
| Reel or Short | high, read them all | Presenter points at the screen. Only a speech-to-text transcript exists, and it mangles names. |
| Screen recording or software demo | high | The screen is the content. Commands and config are never fully spoken. |
| Polished talking-head tutorial | low, read a handful | Real captions, and the presenter reads everything aloud. Don't burn context reading 24 frames of a face. |

**Reconciling:**

- Treat frames as what was **seen** and the transcript as what was **said**. Merge them into one timeline before concluding anything.
- Label each claim you carry into the summary: **confirmed** (both agree), **single source** (only one says it), or **conflict**. **Frames win on anything written down**: repo names, commands, URLs, product names, numbers. Say so when you correct one.
- The **caption** (video description or reel caption) is a third source and often the most reliable for spelling. Use it to settle conflicts.
- **Declare the gaps.** On a 60-second reel a frame every ~5s is near-complete coverage. On a 20-minute tutorial it could be one per minute. Say which situation you're in and offer `--max-frames` if it matters.
- Where the on-screen content *is* the payload (a full prompt, a config block, a command), **transcribe it verbatim** into the capture file. Never paraphrase it into a bullet.

## Step 3: summarise

A few sentences to a short bullet list: what technique, process or tool the video describes, the key steps, what's worth a second look, and explicitly **whether any of it looks skill-worthy** (a repeatable process someone would run again). Mark anything that came only from the screen as `On-screen: ...`.

For long transcripts, summarise directly. Don't paste the raw transcript back into the chat.

## Step 4: save

Save to the `captures_dir` from Step 0 (default `~/video-captures`), creating it if needed:

`<captures_dir>/<slug>.md`, where `<slug>` is kebab-case from the title. Reels have no title, so build one from the creator's handle plus the gist (for example `@creator three-claude-code-tips`).

```markdown
# <video title>

Source: <url>
Captured: <YYYY-MM-DD>
Type: <reel / screen recording / talking head>, <n> frames, one every <interval>s

## Summary

<the summary from step 3>

## Transcript

<the full transcript>
```

Then add a row to `<captures_dir>/index.md`, newest first (create it with a header row if it doesn't exist): date, title linked to the file, source, and one line on what the video is *about*. That index is how a future session answers "have I already captured this?".

Don't copy frames or video into the captures folder. They sit in a temp folder. Only the text is worth keeping.

## Step 5: verdict, and build a skill only on a yes

Tell the user where the file landed and give a one-line verdict.

- **Not skill-worthy:** stop there.
- **Skill-worthy:** ask plainly, "This looks like a repeatable process. Want me to turn it into a skill?" **Never build without an explicit yes.**

If they say yes:

1. If a skill-building skill is installed (for example `skill-creator`), use it and hand it the capture file as the starting context.
2. Otherwise build it yourself:
   - Ask at most three short questions: what should make it trigger, what it should produce, anything from the video to leave out.
   - Write `~/.claude/skills/<kebab-name>/SKILL.md` with frontmatter `name` and a **quoted** `description` that says when to use it (an unquoted description containing a colon followed by a space breaks the whole frontmatter and the skill silently never loads). The body is the repeatable process from the video as numbered steps, with any commands verbatim, plus a `Source:` line linking the capture file.
   - Show the draft before saving.
   - Tell them to start a new Claude Code session if the new skill doesn't show up straight away.

## Example

> User: "capture this https://www.instagram.com/reel/ABC123/"
> -> Step 0 says the Apify token is missing. Claude explains it's only needed for Instagram, gives the signup link, the user pastes the token, Claude saves it with `--save-key`, it verifies.
> -> Capture returns a transcript plus 12 frames. Three tool names and a command were only ever on screen, so they go into the summary marked `On-screen:`.
> -> "Saved to ~/video-captures/creator-reel-to-skill.md. This one walks through a repeatable process. Want me to turn it into a skill?"
