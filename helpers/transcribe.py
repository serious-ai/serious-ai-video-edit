"""Transcribe a video, word-level timestamps, verbatim.

Two engines, same output shape (<edit_dir>/transcripts/<video_stem>.json,
a {"words": [...]} dict of type word/spacing/audio_event entries):

- **local** (default): faster-whisper, runs on-device, no network call, no
  per-minute cost. No diarization or audio-event tagging (speaker_id is
  always null).
- **scribe**: ElevenLabs Scribe, uploads audio, costs ~330 credits/minute.
  Adds diarization + audio-event tags (laughs, sighs) on top of words.
  Use this when you need speaker separation or the free-tier local engine
  isn't accurate enough for the source.

Cached: if the output file already exists, no transcription runs at all
(neither engine is invoked) regardless of which engine you pass.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --engine scribe
    python helpers/transcribe.py <video_path> --model medium
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --engine scribe --num-speakers 2
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import wave
from functools import lru_cache
from pathlib import Path

import requests


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEFAULT_ENGINE = "local"
DEFAULT_WHISPER_MODEL = "small"


def load_api_key() -> str:
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "ELEVENLABS_API_KEY":
                    return v.strip().strip('"').strip("'")
    v = os.environ.get("ELEVENLABS_API_KEY", "")
    if not v:
        sys.exit("ELEVENLABS_API_KEY not found in .env or environment")
    return v


def count_audio_tracks(video_path: Path) -> int:
    """How many audio streams the container holds."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


def peak_dbfs(wav_path: Path) -> float:
    """Peak level of a 16-bit PCM wav, in dBFS. -inf for digital silence."""
    peak = 0
    with wave.open(str(wav_path), "rb") as w:
        # A chunk at a time: batch mode runs several of these at once, and a two-hour
        # take is 230 MB of 16 kHz mono before the array copy doubles it.
        while frames := w.readframes(1 << 16):
            samples = array.array("h", frames)
            peak = max(peak, max(samples), -min(samples))
    return 20 * math.log10(peak / 32768) if peak > 0 else float("-inf")


def extract_audio(video_path: Path, dest: Path, audio_track: int = 0) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-map", f"0:a:{audio_track}",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


@lru_cache(maxsize=2)
def _load_whisper_model(model_size: str):
    """Load (and cache) a faster-whisper model. int8 on CPU: fast enough for
    batch use without a GPU, and the only compute type every Mac can run."""
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


# Whisper's training data is mostly clean captions, so by default it silently
# "cleans up" um/uh/false starts instead of transcribing them -- verified against
# faster-whisper 1.2.1 on both the small and medium models (upstream's own
# anti-patterns note calls this out: "Running Whisper locally ... normalizes
# fillers"). Priming it with a verbatim-style initial_prompt measurably reduces
# this (observed ~75% of dropped fillers recovered in testing) but does not
# eliminate it -- this engine is not a guaranteed-verbatim substitute for Scribe.
# If a project leans hard on filler-word cutting, prefer --engine scribe.
_VERBATIM_PROMPT = (
    "Um, uh, this is, like, a verbatim transcript. Uh, we keep every um and uh "
    "exactly as spoken, um, including false starts."
)


def call_whisper_local(
    audio_path: Path,
    language: str | None = None,
    model_size: str = DEFAULT_WHISPER_MODEL,
) -> dict:
    """Local word-level transcription via faster-whisper. Same `words` shape
    as call_scribe's response, minus diarization and audio-event tags:
    speaker_id is always None, and there are no "audio_event" entries.

    Gaps between words are synthesized as "spacing" entries (start/end only)
    so pack_transcripts.py's silence-based phrase breaking still works.
    """
    model = _load_whisper_model(model_size)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=False,
        condition_on_previous_text=False,
        initial_prompt=_VERBATIM_PROMPT,
    )

    words: list[dict] = []
    prev_end: float | None = None
    for segment in segments:
        for w in segment.words or []:
            text = (w.word or "").strip()
            if not text:
                continue
            if prev_end is not None and w.start > prev_end:
                words.append({"type": "spacing", "start": prev_end, "end": w.start})
            words.append({
                "type": "word",
                "text": text,
                "start": w.start,
                "end": w.end,
                "speaker_id": None,
            })
            prev_end = w.end

    return {"words": words, "language_code": info.language, "engine": "whisper-local", "model": model_size}


def transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path:
    """Where a video's transcript lands.

    The track belongs in the name, or a rerun with --audio-track hands back the transcript of
    the track it is meant to replace. Track 0 keeps the plain name, so transcripts made before
    the flag existed stay valid. Batch mode tests its cache with this too -- one function, so
    the two cannot drift apart.
    """
    suffix = "" if audio_track == 0 else f".track{audio_track}"
    return edit_dir / "transcripts" / f"{video.stem}{suffix}.json"


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str | None = None,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    audio_track: int = 0,
    engine: str = DEFAULT_ENGINE,
    model_size: str = DEFAULT_WHISPER_MODEL,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    n_tracks = count_audio_tracks(video)
    if n_tracks > 1 and verbose:
        print(f"  note: {video.name} has {n_tracks} audio tracks, using track "
              f"{audio_track + 1} (--audio-track to change)", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio, audio_track)

        # Uploading silence costs the same as uploading speech and returns
        # nothing, so catch the wrong-track case before paying for it.
        peak = peak_dbfs(audio)
        if peak < -60.0:
            raise RuntimeError(
                f"track {audio_track + 1} of {video.name} is silent "
                f"(peak {peak:.1f} dBFS) - not uploading. "
                + (f"The file has {n_tracks} audio tracks; try --audio-track "
                   + " or ".join(str(i) for i in range(n_tracks) if i != audio_track) + "."
                   if n_tracks > 1 else "Check the source audio.")
            )

        size_mb = audio.stat().st_size / (1024 * 1024)
        if engine == "scribe":
            if not api_key:
                raise RuntimeError("engine=scribe requires an ElevenLabs API key")
            if verbose:
                print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB) to Scribe", flush=True)
            payload = call_scribe(audio, api_key, language, num_speakers)
        elif engine == "local":
            if verbose:
                print(f"  transcribing {video.stem}.wav ({size_mb:.1f} MB) locally "
                      f"(whisper/{model_size})", flush=True)
            payload = call_whisper_local(audio, language, model_size)
        else:
            raise ValueError(f"unknown engine {engine!r}, expected 'local' or 'scribe'")

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video (local whisper by default, or ElevenLabs Scribe)")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--engine",
        choices=["local", "scribe"],
        default=DEFAULT_ENGINE,
        help="'local' (default): faster-whisper, on-device, free. "
             "'scribe': ElevenLabs, costs credits, adds diarization + audio events.",
    )
    ap.add_argument(
        "--model",
        type=str,
        default=DEFAULT_WHISPER_MODEL,
        help="faster-whisper model size for --engine local "
             "(tiny/base/small/medium/large-v3, default: small).",
    )
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers when known. --engine scribe only "
             "(improves diarization accuracy); ignored for --engine local.",
    )
    ap.add_argument(
        "--audio-track",
        type=int,
        default=0,
        help="Zero-based audio track to transcribe. OBS writes the game on track 0 "
             "and the mic on track 1; without this ffmpeg applies its default audio "
             "stream selection, which picks the track with the most channels.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    api_key = load_api_key() if args.engine == "scribe" else None

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        audio_track=args.audio_track,
        engine=args.engine,
        model_size=args.model,
    )


if __name__ == "__main__":
    main()
