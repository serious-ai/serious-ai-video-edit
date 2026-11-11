#!/usr/bin/env python3
"""Verify a rendered edit against its source: every seam by ear (`listen`), and A/V sync (`sync`).

WHY listen. Ep 326 (Sept 24, 2026) shipped two clipped words to YouTube and Spotify: cut points
taken from sentence timings landed inside the last word ("conferences" -> "con-"). Reading
the joined transcript does not catch that, and neither word timings (whisper drifts 0.2 s or
more either way) nor pause detection (this speaker runs words together) can place a cut
reliably on their own. The rendered audio is the ground truth.

WHY sync. Ep 333 (Nov 11, 2026) shipped with the audio sliding ahead of the picture, +26 ms at
0:33 to +165 ms at 13:31, and Spotify flagged it. Per-segment AAC files drifted at each join,
and a hand rebuild that assumed 30 fps (the source averaged 29.995) kept the drift. sync
measures it instead of assuming: at ~7 points it takes where the picture is in the source
(the render's own video timestamps, mapped through each segment's real frame count and
first source frame) and where the audio is (cross-correlation of the rendered audio against
the source audio), and prints the difference. Positive = audio leads the picture.
The rendered audio is decoded start to finish and placed by sample count, the way a player
hears it: the old per-segment render looked in sync to a timestamp-based `ffmpeg -ss` read,
while its extra AAC priming frames played back to back pushed the audio 13 ms later per join.

USAGE
  seam_check.py listen <edl.json> <render.mp4> [--model medium]
  seam_check.py sync   <edl.json> <render.mp4> [source] [--clips DIR] [--points 7]
listen: run it on a fast `render.py --draft` first, fix any seam where the last word before the |
or the first word after it is missing or changed, re-draft, and only then render --native.
sync: run it on the master before any upload. It exits 1 if any offset differs from the first by
more than 40 ms (drift) or the packaging fails Spotify's checks (one stereo audio stream,
initial_padding 0, every decoded frame stereo, audio within 100 ms of the video length), and 2 if it
could not measure. It needs the segment clips render.py left
next to the EDL (clips_graded/ for a full or --native render); --clips picks another folder.
"""
import sys, json, subprocess, tempfile, os, argparse
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SR = 8000          # correlation sample rate: 0.125 ms resolution
WINDOW = 3.0       # seconds of rendered audio matched at each point
SEARCH = 0.5       # +/- seconds searched in the source around the expected position
MIN_CORR = 0.5     # weaker matches (silence, music bed) are reported but not judged


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


# -------- sync -----------------------------------------------------------------


def _video_pts(path):
    """Sorted presentation times of every video frame, on the timeline `ffmpeg -ss` uses."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "packet=pts_time:format=start_time", "-of", "json", path],
                         capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    t0 = float(data.get("format", {}).get("start_time") or 0.0)
    return sorted(float(p["pts_time"]) - t0 for p in data["packets"] if p.get("pts_time") not in (None, "N/A"))


def _audio(path, start=None, dur=None):
    """Mono SR float audio. With no start, the whole stream decoded contiguously (player view)."""
    import numpy as np
    span = [] if start is None else ["-ss", f"{max(0.0, start):.6f}"]
    length = [] if dur is None else ["-t", f"{dur:.6f}"]
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *span, "-i", path, *length,
                          "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True)
    return np.frombuffer(out.stdout, np.float32)


def _match(needle, hay):
    """Best normalized cross-correlation of needle inside hay: (lag in samples, score 0..1)."""
    import numpy as np
    a = needle.astype(np.float64); b = hay.astype(np.float64)
    a = a - a.mean(); b = b - b.mean()
    n = len(a); m = len(b) - n + 1
    if m < 1 or not a.any():
        return 0, 0.0
    size = 1 << (len(b) + n).bit_length()
    c = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)[:m]
    e = np.concatenate(([0.0], np.cumsum(b * b)))
    norm = np.sqrt(np.maximum(e[n:n + m] - e[:m], 1e-12)) * np.sqrt((a * a).sum())
    score = c / norm
    k = int(np.argmax(score))
    return k, float(score[k])


def _find_clips(edl, edl_dir, n_render_frames, clips_arg):
    import render as R
    names = [f"seg_{i:02d}_{r['source']}.mp4" for i, r in enumerate(edl["ranges"])]
    dirs = [Path(clips_arg)] if clips_arg else [edl_dir / d for d in ("clips_graded", "clips_preview", "clips_draft")]
    best = None
    for d in dirs:
        paths = [d / n for n in names]
        if not all(p.exists() for p in paths):
            continue
        frames = [R.probe_video_frames(p) for p in paths]
        gap = abs(sum(frames) - n_render_frames)
        if best is None or gap < best[0]:
            best = (gap, d, paths)
        if gap == 0:
            break
    return best


def _packaging(path):
    """Spotify for Creators packaging checks (tested Nov 11, 2026). Returns [(ok, message)]."""
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                       "stream=codec_type,channels,initial_padding,start_time,duration",
                                       "-of", "json", path], capture_output=True, text=True, check=True).stdout)
    audio = [st for st in probe["streams"] if st.get("codec_type") == "audio"]
    video = [st for st in probe["streams"] if st.get("codec_type") == "video"]
    out = [(len(audio) == 1, f"{len(audio)} audio stream(s), need exactly 1")]
    if len(audio) != 1 or not video:
        return out
    a, v = audio[0], video[0]
    out.append((int(a.get("channels") or 0) == 2, f"{a.get('channels')} channels, need 2"))
    out.append((int(a.get("initial_padding") or 0) == 0,
                f"initial_padding {a.get('initial_padding') or 0}, need 0 (no priming edit list)"))
    frames = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "frame=channels",
                             "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout.split()
    odd = sum(1 for c in frames if c.strip(",") != "2")
    out.append((bool(frames) and odd == 0, f"{len(frames)} decoded frames, {odd} not stereo"))
    end = lambda st: float(st.get("start_time") or 0) + float(st.get("duration") or 0)
    gap = end(a) - end(v)
    out.append((abs(gap) <= 0.1, f"audio ends {gap * 1000:+.0f} ms from video, limit 100 ms"))
    return out


def sync(edl_path, render_path, source, clips_arg, n_points, tol_ms):
    import numpy as np
    import render as R
    edl_path = Path(edl_path).resolve(); edl_dir = edl_path.parent
    edl = json.loads(edl_path.read_text())
    if source:
        if len(edl["sources"]) != 1:
            sys.exit("this EDL has several sources; leave [source] off and the EDL's own paths are used")
        edl["sources"] = {k: str(Path(source).resolve()) for k in edl["sources"]}

    pts = _video_pts(render_path)
    found = _find_clips(edl, edl_dir, len(pts), clips_arg)
    if not found:
        print("sync: no segment clips found next to the EDL (clips_graded/, clips_preview/, clips_draft/); "
              "pass --clips", file=sys.stderr)
        return 2
    gap, clips_dir, clip_paths = found
    rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=r_frame_rate", "-of", "csv=p=0", str(clip_paths[0])],
                          capture_output=True, text=True, check=True).stdout.strip()
    tl = R.segment_timeline(edl, edl_dir, clip_paths, rate)
    fr = float(Fraction(rate))
    cum = np.cumsum([0] + [t["frames"] for t in tl])
    print(f"sync: {render_path}")
    print(f"  {len(tl)} segments from {clips_dir.name}/ @ {fr:.4f} fps, {len(pts)} frames in render"
          + (f"  (WARNING: segments total {int(cum[-1])} frames, render has {len(pts)})" if gap else ""))

    def picture_raw(T):
        """Source time of the picture on screen at render time T, and its segment index."""
        k = max(0, min(int(np.searchsorted(pts, T, side="right")) - 1, int(cum[-1]) - 1))
        i = int(np.searchsorted(cum, k, side="right")) - 1
        return tl[i]["raw_start"] + (k - cum[i]) / fr + (T - pts[k]), i

    # Pick ~n points spread across the file, each with a WINDOW that stays inside one segment.
    seg_t0 = [pts[int(c)] for c in cum[:-1]]
    long_segs = [i for i, t in enumerate(tl) if t["duration"] >= WINDOW + 0.4]
    if not long_segs:
        print("sync: no segment is long enough to measure", file=sys.stderr)
        return 2
    points = []
    for p in range(n_points):
        T = pts[-1] * (p + 0.5) / n_points
        _, i = picture_raw(T)
        if i not in long_segs or T + WINDOW + 0.2 > seg_t0[i] + tl[i]["duration"]:
            i = min(long_segs, key=lambda j: abs(seg_t0[j] + tl[j]["duration"] / 2 - T))
            T = seg_t0[i] + max(0.2, min(tl[i]["duration"] / 2 - WINDOW / 2, tl[i]["duration"] - WINDOW - 0.2))
        if all(abs(T - q) > WINDOW for q in points):
            points.append(T)

    # Player view: every decoded sample, back to back, from the audio stream's start time.
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                       "stream=start_time:format=start_time", "-of", "json",
                                       render_path], capture_output=True, text=True, check=True).stdout)
    a_start = float(probe["streams"][0].get("start_time") or 0) - float(probe["format"].get("start_time") or 0)
    played = _audio(render_path)

    rows = []
    for T in sorted(points):
        raw, i = picture_raw(T)
        src = tl[i]["source"]
        n0 = int(round((T - a_start) * SR))
        needle = played[max(0, n0):n0 + int(WINDOW * SR)]
        hay = _audio(src, raw - SEARCH, WINDOW + 2 * SEARCH)
        k, score = _match(needle, hay)
        off_ms = ((raw - SEARCH + k / SR) - raw) * 1000
        rows.append((T, raw, off_ms, score))

    good = [r for r in rows if r[3] >= MIN_CORR]
    ref = good[0][2] if good else None
    print(f"  {'render':>8}  {'source':>9}  {'offset':>9}  {'vs first':>8}  match")
    worst = 0.0
    for T, raw, off, score in rows:
        mark = ""
        if score < MIN_CORR:
            mark = "  weak match, not judged"
            rel = "    -"
        else:
            d = off - ref; worst = max(worst, abs(d))
            rel = f"{d:+7.1f}"
            if abs(d) > tol_ms: mark = "  DRIFT"
        print(f"  {int(T//60):>4}:{T%60:05.2f}  {raw:9.3f}  {off:+7.1f}ms  {rel}ms  {score:.2f}{mark}")
    pack = _packaging(render_path)
    print("  packaging (Spotify): " + "  ".join(("ok" if ok else "FAIL") + f" {msg}" for ok, msg in pack))
    if len(good) < 3:
        print(f"FAIL: only {len(good)} point(s) matched cleanly, sync not verified")
        return 2
    if not all(ok for ok, _ in pack):
        print("FAIL: packaging differs from the file Spotify accepted on Nov 11, 2026. Re-render with the current render.py "
              "(one ADTS AAC encode, stream-copied).")
        return 1
    if ref is not None and abs(ref) > tol_ms:
        print(f"  note: constant offset of {ref:+.0f} ms at the first point")
    if worst > tol_ms:
        print(f"FAIL: offset moves by up to {worst:.0f} ms across the file (limit {tol_ms:.0f} ms). "
              "Audio and video drift apart. Do not upload; re-render with the current render.py.")
        return 1
    print(f"PASS: offset steady within {worst:.0f} ms across {len(good)} points (limit {tol_ms:.0f} ms)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("listen", help="transcribe 8 s around every seam")
    pl.add_argument("edl"); pl.add_argument("render"); pl.add_argument("--model", default="medium")
    ps = sub.add_parser("sync", help="measure audio/video sync against the source")
    ps.add_argument("edl"); ps.add_argument("render"); ps.add_argument("source", nargs="?")
    ps.add_argument("--clips", help="segment clips folder (default: auto-detect next to the EDL)")
    ps.add_argument("--points", type=int, default=7)
    ps.add_argument("--tolerance-ms", type=float, default=40.0)
    a = ap.parse_args()
    if a.cmd == "listen":
        listen(a.edl, a.render, a.model)
    else:
        sys.exit(sync(a.edl, a.render, a.source, a.clips, a.points, a.tolerance_ms))
