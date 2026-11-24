"""A film's segments joined into one clip (the generator's Film mode; docs/FILM_MODE.md). Each pair of slots is drawn as
its own video (h3-film.json) and kept in Generated; when the last one lands the page asks for them joined, in slot
order, into one MP4 that is kept in Generated with them (the same batch) and can be published to a screen like any
clip. With the sound each segment drew; a segment without sound (Sound: silent) gets silence for its length."""
import json
import subprocess
import tempfile
from pathlib import Path

from utils.logger import info


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=30).stdout
    data = json.loads(out or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError(f"{path.name} is not a video")
    num, _, den = str(video.get("r_frame_rate") or "24/1").partition("/")
    return {"width": int(video["width"]), "height": int(video["height"]),
            "fps": float(num) / float(den or 1),
            "audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
            "duration": float(data.get("format", {}).get("duration") or 0)}


def join_command(clips: list[Path], out: Path, probes: list[dict]) -> list[str]:
    """ffmpeg's command: every clip at the first one's size and frame rate, one after the other, with its sound (or
    silence where it has none; no sound track at all when none has one)."""
    first = probes[0]
    w, h, fps = first["width"], first["height"], first["fps"]
    sound = any(p["audio"] for p in probes)
    cmd = ["nice", "-n", "10", "ffmpeg", "-v", "error", "-y"]
    for clip in clips:
        cmd += ["-i", str(clip)]
    silent_inputs = {}
    for i, p in enumerate(probes):
        if sound and not p["audio"]:
            silent_inputs[i] = len(clips) + len(silent_inputs)
            cmd += ["-f", "lavfi", "-t", f"{p['duration']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    parts, chain = [], []
    for i in range(len(clips)):
        parts.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                     f"setsar=1,fps={fps:g}[v{i}]")
        if sound:
            src = f"{silent_inputs[i]}:a" if i in silent_inputs else f"{i}:a"
            parts.append(f"[{src}]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
            chain.append(f"[v{i}][a{i}]")
        else:
            chain.append(f"[v{i}]")
    parts.append(f"{''.join(chain)}concat=n={len(clips)}:v=1:a={1 if sound else 0}[v]" + ("[a]" if sound else ""))
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if sound:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(out)]
    return cmd


def join(clips: list[Path], out: Path) -> Path:
    """Join `clips`, in order, into `out` (an MP4)."""
    if len(clips) < 2:
        raise ValueError("a film needs two clips or more to join")
    probes = [_probe(c) for c in clips]
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".mp4", dir=out.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        cmd = join_command(clips, tmp_path, probes)
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if done.returncode != 0:
            raise RuntimeError(f"ffmpeg could not join the film: {done.stderr.strip()[-300:]}")
        tmp_path.replace(out)
    finally:
        tmp_path.unlink(missing_ok=True)
    info(f"[film] joined {len(clips)} clips into {out.name} ({sum(p['duration'] for p in probes):.1f} s)")
    return out
