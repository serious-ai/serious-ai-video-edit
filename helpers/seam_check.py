#!/usr/bin/env python3
"""Verify every seam of a rendered edit by ear: transcribe 8 s around each cut, print the join.

WHY. Ep 326 (Sept 24, 2026) shipped two clipped words to YouTube and Spotify: cut points
taken from sentence timings landed inside the last word ("conferences" -> "con-"). Reading
the joined transcript does not catch that, and neither word timings (whisper drifts 0.2 s or
more either way) nor pause detection (this speaker runs words together) can place a cut
reliably on their own. The rendered audio is the ground truth.

USAGE
  seam_check.py listen <edl.json> <render.mp4> [--model medium]
Run it on a fast `render.py --draft` first, fix any seam where the last word before the |
or the first word after it is missing or changed, re-draft, and only then render --native.
"""
import sys, json, subprocess, tempfile, os, argparse


def listen(edl_path, master, model):
    from faster_whisper import WhisperModel
    rs = json.load(open(edl_path))["ranges"]; pos = 0; seams = []
    for i, r in enumerate(rs[:-1]):
        pos += r["end"] - r["start"]; seams.append(pos)
    m = WhisperModel(model, device="cpu", compute_type="int8")
    with tempfile.TemporaryDirectory() as d:
        for n, t in enumerate(seams, 1):
            f = os.path.join(d, f"s{n}.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(max(0, t - 4)), "-t", "8", "-i", master,
                            "-map", "0:a", "-ac", "1", "-ar", "16000", f], check=True)
            segs, _ = m.transcribe(f, word_timestamps=True, language="en")
            ws = [w for s in segs for w in s.words]; out = []; marked = False
            for w in ws:
                if not marked and w.start >= 4 - 0.02: out.append("|"); marked = True
                out.append(w.word.strip())
            print(f"seam {n} at {int(t//60)}:{t%60:05.2f}  ::  {' '.join(out)}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["listen"]); ap.add_argument("edl")
    ap.add_argument("render"); ap.add_argument("--model", default="medium"); a = ap.parse_args()
    listen(a.edl, a.render, a.model)
