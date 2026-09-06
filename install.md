---
name: video-use-local-install
description: Install video-use-local into the current agent (Claude Code, Codex, Hermes, Openclaw, etc.) and wire up ffmpeg so the user can start editing immediately, free, with no API key required.
---

# video-use-local install

Use this file only for first-time install or reconnect. For daily editing, read `SKILL.md`. Always read `helpers/` — that's where the scripts live.

This is a community fork of [browser-use/video-use](https://github.com/browser-use/video-use) (MIT licensed). The only functional difference: transcription defaults to a local, on-device Whisper model instead of ElevenLabs Scribe, so there's no API key and no per-minute cost to get started. Everything else — cutting, grading, subtitles, animations — is unchanged.

## What you're doing

You're setting up a conversation-driven video editor for the user. After install, the user drops raw footage into any folder, runs their agent (`claude`, `codex`, etc.) there, and says "edit these into a launch video." You do the rest by reading `SKILL.md`.

Three things must exist on this machine:

1. This repo, available somewhere stable (cloned, or already sitting where you're reading this from — see Step 1).
2. `ffmpeg` on `$PATH` (plus optional `yt-dlp` for online sources).
3. Python deps installed, including `faster-whisper` for local transcription (no API key needed for this).

And one thing must be true about the current agent:

4. It can discover `SKILL.md` — either via a global skills directory (`~/.claude/skills/`, `~/.codex/skills/`) or via a `CLAUDE.md` / system-prompt import.

An ElevenLabs API key is **optional** — only needed if the user explicitly wants Scribe's speaker diarization or audio-event tagging (see Step 5). Don't ask for it otherwise; most users never need it.

## Install prompt contract

- Do everything yourself. Only ask the user for things you cannot generate — confirmation before `brew install`, and (only if they ask for Scribe) an ElevenLabs API key.
- The skill references helpers by bare name (`transcribe.py`, `render.py`). That works because SKILL.md and `helpers/` ship together — keep them as siblings wherever this ends up.
- After install, verify by running one real command against one real file. Don't declare success on file-existence checks alone.

## Steps

### 1. Get the repo onto this machine

Check where you're reading this file from before doing anything:

```bash
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
```

- **If `helpers/` already exists next to this file** (`$INSTALL_DIR/helpers`), you're already running from a fully-populated copy — e.g. the user installed a `.skill` package that dropped this whole directory directly into their agent's skills folder. Don't clone anything. Just treat `$INSTALL_DIR` as the install location and skip to Step 2.
- **Otherwise**, clone it fresh to a stable path (not `/tmp`, not `~/Downloads`):

    ```bash
    test -d ~/Developer/video-use-local || git clone https://github.com/serious-ai/video-use-local ~/Developer/video-use-local
    cd ~/Developer/video-use-local
    ```

    If it's already there, `git pull --ff-only` and continue. From here on, `$INSTALL_DIR` = `~/Developer/video-use-local`.

### 2. Install Python deps

macOS ships Python 3.9, which is too old (this needs ≥3.10) and Homebrew's Python is externally-managed, so a plain `pip install` will fail with an `externally-managed-environment` error. Use a dedicated venv:

```bash
cd "$INSTALL_DIR"
command -v uv >/dev/null && uv sync && exit 0   # uv handles the venv itself

# no uv: use whichever python3.10+ is available, in a venv
PY=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/pip install -e .
```

`pyproject.toml` lists `requests`, `librosa`, `matplotlib`, `pillow`, `numpy`, `faster-whisper`. No console scripts — helpers are invoked as `<python> helpers/<name>.py`, where `<python>` is `.venv/bin/python` if you created a venv, or plain `python3`/`uv run python` otherwise. Note which one applies for this machine — you'll need it in Step 6 and every future session.

### 3. Install ffmpeg (+ optional yt-dlp)

`ffmpeg` and `ffprobe` are hard requirements. `yt-dlp` is only needed if the user wants to pull sources from URLs. Animation engines such as HyperFrames, Remotion, and Manim are installed lazily the first time a project actually needs them.

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v yt-dlp >/dev/null || brew install yt-dlp     # optional

# Debian / Ubuntu
# sudo apt-get update && sudo apt-get install -y ffmpeg
# pip install yt-dlp

# Arch
# sudo pacman -S ffmpeg yt-dlp
```

If `brew` / `apt` / `pacman` requires a sudo prompt, tell the user the exact command and wait. Do not invent a password.

First local transcription also downloads a small Whisper model (~500MB for the default `small` size) from Hugging Face on first use — one-time, cached afterward. That happens automatically the first time `transcribe.py` runs; no action needed here.

### 4. Register the skill with the current agent

If Step 1 found you already living inside an agent's skills directory (the `.skill`-package case), **skip this step entirely** — you're already registered.

Otherwise, figure out which agent you're running under and register once. A symlink of the whole repo directory is the right shape — helpers/ needs to sit next to SKILL.md.

- **Claude Code** (`~/.claude/` present):

    ```bash
    mkdir -p ~/.claude/skills
    ln -sfn "$INSTALL_DIR" ~/.claude/skills/video-use-local
    ```

- **Codex** (`$CODEX_HOME` set, or `~/.codex/` present):

    ```bash
    mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
    ln -sfn "$INSTALL_DIR" "${CODEX_HOME:-$HOME/.codex}/skills/video-use-local"
    ```

- **Hermes / Openclaw / another agent with a skills directory**: symlink `$INSTALL_DIR` into that agent's skills directory under the name `video-use-local`. If the agent has no skills directory, add a line to its system prompt / config pointing at `$INSTALL_DIR/SKILL.md` (e.g. an `@$INSTALL_DIR/SKILL.md` import in a `CLAUDE.md`-equivalent).

If you can't tell which agent you're in, ask the user once: "which agent am I running under — Claude Code, Codex, or something else?" Then pick the right target.

### 5. ElevenLabs API key (optional — skip unless asked)

Transcription works out of the box with the local engine — nothing to configure. **Do not ask the user for an ElevenLabs key during install.** Only set this up if the user later asks specifically for speaker diarization or audio-event tags (laughs, sighs), which the local engine doesn't do.

If that comes up:

1. Check existing state in this order and stop at the first hit:

    ```bash
    [ -n "$ELEVENLABS_API_KEY" ] && echo "env"
    grep -q '^ELEVENLABS_API_KEY=..' "$INSTALL_DIR/.env" 2>/dev/null && echo "dotenv"
    ```

2. If neither is set, ask the user exactly once:

    > For that I'd use ElevenLabs Scribe instead of the local engine — it costs API credits (~330 per minute of audio, 10k free/month) but adds speaker diarization and audio-event tags. Grab a key at https://elevenlabs.io/app/settings/api-keys and paste it here, or say never mind to stick with the free local engine.

    When the user pastes a key, write it to `.env`:

    ```bash
    printf 'ELEVENLABS_API_KEY=%s\n' "$KEY" > "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
    ```

    Never echo the key back in tool output. Never commit `.env`.

3. Sanity check with a cheap, quota-free call:

    ```bash
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "xi-api-key: $(sed -n 's/^ELEVENLABS_API_KEY=//p' "$INSTALL_DIR/.env")" \
      https://api.elevenlabs.io/v1/user
    ```

    `200` means the key works. `401` means the user pasted a wrong/expired key — ask once more and stop.

### 6. Verify end-to-end

Run one real thing. Prefer the lightest verification that still proves the pipeline is wired up (substitute your actual python — `.venv/bin/python`, `uv run python`, or `python3` — for `$PYTHON` below):

```bash
$PYTHON "$INSTALL_DIR/helpers/timeline_view.py" --help >/dev/null && echo "helpers OK"
ffprobe -version | head -1
```

A full local transcription test is cheap (no API cost) and worth running once if you have any short clip handy — it also triggers the one-time model download so the user's first real session isn't slower than expected. Not required if no test clip is available; the pipeline verifying via `--help` is enough to hand off.

### 7. Hand off

Tell the user, in one short message:

- Where the skill is installed (`$INSTALL_DIR`).
- That transcription is local and free by default — no API key needed.
- **One honest caveat, worth surfacing up front:** the local engine sometimes cleans up "um"/"uh" and false starts instead of keeping them verbatim (a known Whisper behavior — it's mitigated but not eliminated here). If they're doing heavy filler-word cutting on an important project, mention they can ask for the ElevenLabs engine instead (Step 5) for guaranteed-verbatim transcription.
- That they should `cd` into their footage folder and start their agent there (e.g. `claude`).
- That a good first message is: *"edit these into a launch video"* or *"inventory these takes and propose a strategy."*
- That all outputs land in `<videos_dir>/edit/` — the repo stays clean.

## Keeping the skill current

- `cd "$INSTALL_DIR" && git pull --ff-only` pulls the latest code (skip if installed via `.skill` package — update by installing a newer package instead). The symlink auto-picks it up on the next run.
- If `pyproject.toml` changed deps, re-run `uv sync` / `.venv/bin/pip install -e .` after pulling.

## Cold-start reminders

- Symlink the **whole directory**, not just `SKILL.md`. The helpers need to sit next to it.
- Local transcription needs no key at all — don't gate first use on asking for one.
- If `.env` exists but the ElevenLabs key is empty, treat it the same as missing — don't assume existence means validity.
- `ffmpeg` from static builds works fine. Any modern (≥ 4.x) build is enough.
- `yt-dlp` is optional. Don't block install on it; install lazily the first time a user asks to pull from a URL.
- Node.js/npm are only needed for HyperFrames or Remotion slots. HyperFrames currently requires Node.js 22+.
- HyperFrames, Remotion, and Manim are optional animation engines. Don't install or prefer one globally during setup; pick the engine per animation slot in `SKILL.md`. HyperFrames can run through `npx --yes hyperframes ...` in the slot directory. Remotion can be scaffolded with `npx create-video@latest` or installed inside the slot before rendering.
- If the user is on Linux without a package manager Claude recognizes, print the manual `ffmpeg` install URL and wait rather than guessing.
