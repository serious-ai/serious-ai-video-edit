<p align="center">
  <img src="static/video-use-banner.png" alt="video-use-local" width="100%">
</p>

# video-use-local

Edit videos with Claude Code, by conversation. **A community fork of [browser-use/video-use](https://github.com/browser-use/video-use)** (same editor, but transcription defaults to a free, on-device Whisper model instead of ElevenLabs Scribe, so there's no API key and no per-minute cost to get started).

Drop raw footage in a folder, chat with Claude Code, get `final.mp4` back. Works for any content (talking heads, montages, tutorials, travel, interviews) without presets or menus.

## What it does

- **Cuts out filler words** (`umm`, `uh`, false starts) and dead space between takes
- **Auto color grades** every segment (warm cinematic, neutral punch, or any custom ffmpeg chain)
- **30ms audio fades** at every cut so you never hear a pop
- **Burns subtitles** in your style (2-word UPPERCASE chunks by default, fully customizable)
- **Generates animation overlays** via [HyperFrames](https://github.com/heygen-com/hyperframes), [Remotion](https://www.remotion.dev/), [Manim](https://www.manim.community/), or PIL, spawned in parallel sub-agents, one per animation
- **Self-evaluates the rendered output** at every cut boundary before showing you anything
- **Persists session memory** in `project.md` so next week's session picks up where you left off

## How this fork differs from upstream

| | upstream (`browser-use/video-use`) | this fork (`video-use-local`) |
|---|---|---|
| Transcription | ElevenLabs Scribe, always | Local Whisper by default (`--engine local`), free, no key |
| Speaker diarization / audio events | Yes, always | Only with `--engine scribe` (opt-in) |
| Cost to try it | ~330 ElevenLabs credits/min of footage | $0 |
| Setup | Needs an ElevenLabs API key on day one | No key needed to start editing |

**One honest tradeoff:** local Whisper's training data is mostly clean captions, so out of the box it tends to "clean up" `um`/`uh`/false starts instead of transcribing them verbatim, which matters if you lean hard on automatic filler-word cutting. This fork mitigates that with a verbatim-priming prompt (recovers most of it, not all, measured ~75% in testing). If you're cutting something where every "um" needs to be caught, either spot-check the transcript against the source, or ask your agent to use `--engine scribe` for that project instead. The option is still there, you just don't need it to get started.

Everything else (cutting, grading, subtitles, animations, self-eval) is unchanged from upstream.

## Setup prompt

Paste into Claude Code, Codex, Hermes, Openclaw, or any agent with shell access:

```text
Set up https://github.com/serious-ai/video-use-local for me.

Read install.md first to install this repo and wire up ffmpeg, then register the skill with whichever agent you're running under. Transcription is local and free by default. No API key needed. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own, just tell me it's ready and wait for me to drop footage into a folder.
```

The agent handles the clone, dependencies, and skill registration (no account, no API key, no payment info needed to get started).

Then point your agent at a folder of raw takes:

```bash
cd /path/to/your/videos
claude    # or codex, hermes, etc.
```

And in the session:

> edit these into a launch video

It inventories the sources, proposes a strategy, waits for your OK, then produces `edit/final.mp4` next to your sources. All outputs live in `<videos_dir>/edit/`. The skill directory stays clean.

## Manual install

If you'd rather do it by hand:

```bash
# 1. Clone and symlink into your agent's skills directory
git clone https://github.com/serious-ai/video-use-local ~/Developer/video-use-local
ln -sfn ~/Developer/video-use-local ~/.claude/skills/video-use-local        # Claude Code
# ln -sfn ~/Developer/video-use-local ~/.codex/skills/video-use-local       # Codex

# 2. Install deps (macOS: system Python is too old, and Homebrew's is
#    externally-managed, so use a venv, see install.md for the full version)
cd ~/Developer/video-use-local
python3.12 -m venv .venv && .venv/bin/pip install -e .   # or: uv sync
brew install ffmpeg             # required
brew install yt-dlp             # optional, for downloading online sources

# 3. That's it. No API key needed. Only if you want ElevenLabs Scribe
#    (speaker diarization, audio-event tags) instead of the local engine:
cp .env.example .env
$EDITOR .env                    # ELEVENLABS_API_KEY=...
```

## How it works

The LLM never watches the video. It **reads** it, through two layers that together give it everything it needs to cut with word-boundary precision.

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view composite (filmstrip + speaker track + waveform + word labels + silence-gap cut candidates)" width="100%">
</p>

**Layer 1. Audio transcript (always loaded).** One transcription pass per source (local Whisper by default, or ElevenLabs Scribe if you opt in) gives word-level timestamps (plus speaker diarization and audio events like `(laughter)`/`(applause)` when using Scribe). All takes pack into a single ~12KB `takes_packed.md`, the LLM's primary reading view.

```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

**Layer 2. Visual composite (on demand).** `timeline_view` produces a filmstrip + waveform + word labels PNG for any time range. Called only at decision points (ambiguous pauses, retake comparisons, cut-point sanity checks).

> Naive approach: 30,000 frames × 1,500 tokens = **45M tokens of noise**.
> video-use: **12KB text + a handful of PNGs**.

Same idea as browser-use giving an LLM a structured DOM instead of a screenshot, but for video.

## Pipeline

```
Transcribe ──> Pack ──> LLM Reasons ──> EDL ──> Render ──> Self-Eval
                                                              │
                                                              └─ issue? fix + re-render (max 3)
```

The self-eval loop runs `timeline_view` on the _rendered output_ at every cut boundary (catches visual jumps, audio pops, hidden subtitles). You see the preview only after it passes.

## Design principles

1. **Text + on-demand visuals.** No frame-dumping. The transcript is the surface.
2. **Audio is primary, visuals follow.** Cuts come from speech boundaries and silence gaps.
3. **Ask → confirm → execute → self-eval → persist.** Never touch the cut without strategy approval.
4. **Zero assumptions about content type.** Look, ask, then edit.
5. **12 hard rules, artistic freedom elsewhere.** Production-correctness is non-negotiable. Taste isn't.

See [`SKILL.md`](./SKILL.md) for the full production rules and editing craft.

## Credits & license

All editing logic, hard rules, and craft come from [browser-use/video-use](https://github.com/browser-use/video-use). This fork only swaps the default transcription engine. MIT licensed (see `LICENSE`); if this fork is useful to you, consider starring the original too.
