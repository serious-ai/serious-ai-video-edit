"""Batch-transcribe every video in a directory.

Walks <videos_dir> for common video extensions and writes transcripts to
<videos_dir>/edit/transcripts/<name>.json. Defaults to local whisper
(free, on-device, run serially -- a single transcription already uses all
CPU cores, so parallel workers don't help and are forced to 1). Pass
--engine scribe to use ElevenLabs instead (paid, parallelizable, adds
diarization + audio events) with up to --workers concurrent uploads.

Cached per-file: any source that already has a transcript is skipped.

Usage:
    python helpers/transcribe_batch.py <videos_dir>
    python helpers/transcribe_batch.py <videos_dir> --engine scribe --workers 4
    python helpers/transcribe_batch.py <videos_dir> --model medium
    python helpers/transcribe_batch.py <videos_dir> --engine scribe --num-speakers 2
    python helpers/transcribe_batch.py <videos_dir> --edit-dir /custom/edit
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from transcribe import DEFAULT_ENGINE, DEFAULT_WHISPER_MODEL, load_api_key, transcribe_one, transcript_path


VIDEO_EXTS = {".mp4", ".MP4", ".mov", ".MOV", ".mkv", ".MKV", ".avi", ".AVI", ".m4v"}


def find_videos(videos_dir: Path) -> list[Path]:
    videos = sorted(
        p for p in videos_dir.iterdir()
        if p.is_file() and p.suffix in VIDEO_EXTS
    )
    return videos


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel batch transcription of a videos directory")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <videos_dir>/edit)",
    )
    ap.add_argument(
        "--engine",
        choices=["local", "scribe"],
        default=DEFAULT_ENGINE,
        help="'local' (default): faster-whisper, on-device, free, forced serial. "
             "'scribe': ElevenLabs, costs credits, parallelizable, adds diarization + audio events.",
    )
    ap.add_argument(
        "--model",
        type=str,
        default=DEFAULT_WHISPER_MODEL,
        help="faster-whisper model size for --engine local (default: small).",
    )
    ap.add_argument("--workers", type=int, default=4,
                     help="Parallel workers for --engine scribe (default: 4). Ignored, forced to 1, for --engine local.")
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code. Omit to auto-detect per file.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers. --engine scribe only; improves diarization when known.",
    )
    ap.add_argument(
        "--audio-track",
        type=int,
        default=0,
        help="Zero-based audio track to transcribe (OBS: 0 = game, 1 = mic).",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    already_cached = [v for v in videos
                      if transcript_path(edit_dir, v, args.audio_track).exists()]
    pending = [v for v in videos if v not in already_cached]

    print(f"found {len(videos)} videos ({len(already_cached)} cached, {len(pending)} to transcribe)")
    if not pending:
        print("nothing to do")
        return

    api_key = load_api_key() if args.engine == "scribe" else None
    workers = args.workers if args.engine == "scribe" else 1

    print(f"transcribing {len(pending)} files with {workers} parallel worker(s) "
          f"(engine={args.engine}{'/' + args.model if args.engine == 'local' else ''})")
    t0 = time.time()

    errors: list[tuple[Path, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                transcribe_one,
                video=v,
                edit_dir=edit_dir,
                api_key=api_key,
                language=args.language,
                num_speakers=args.num_speakers,
                verbose=False,
                audio_track=args.audio_track,
                engine=args.engine,
                model_size=args.model,
            ): v
            for v in pending
        }
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                out = fut.result()
                print(f"  + {v.stem}  →  {out.name}")
            except Exception as e:
                errors.append((v, str(e)))
                print(f"  x {v.stem}  FAILED: {e}")

    dt = time.time() - t0
    print(f"\ndone in {dt:.1f}s")
    if errors:
        print(f"{len(errors)} failures:")
        for v, msg in errors:
            print(f"  {v.name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
