"""Deterministic local media inspection, thumbnails and evidence frames."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def run(args, timeout=120):
    try:
        result = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError("媒体处理超时，请使用更短的素材后重试") from None
    if result.returncode:
        raise ValueError("无法处理此媒体文件，请检查文件是否完整或使用常见视频、图片、音频格式")
    return result


def probe(path):
    path = Path(path)
    try:
        with Image.open(path) as image:
            # Still images are intentionally a single visual asset (GIF uses its first frame).
            rotated = ImageOps.exif_transpose(image)
            return {"kind": "image", "duration": 0.0, "width": rotated.width, "height": rotated.height, "has_audio": False, "format": image.format.lower()}
    except (OSError, ValueError):
        pass
    result = run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", path], timeout=45)
    try:
        metadata = json.loads(result.stdout)
        streams = metadata.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if video is None and audio is None:
            raise ValueError("没有可用的音视频轨道")
        raw_duration = metadata.get("format", {}).get("duration") or (video or audio).get("duration") or 0
        duration = float(raw_duration)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("媒体时长无效")
        width, height = (int(video.get("width", 0)), int(video.get("height", 0))) if video else (0, 0)
        rotation = 0
        if video:
            for item in video.get("side_data_list", []):
                rotation = int(item.get("rotation", rotation))
            rotation = int(video.get("tags", {}).get("rotate", rotation))
        if abs(rotation) % 180 == 90:
            width, height = height, width
        return {"kind": "video" if video else "audio", "duration": round(duration, 4), "width": width, "height": height, "has_audio": audio is not None, "format": metadata.get("format", {}).get("format_name", ""), "video_codec": video.get("codec_name") if video else None, "audio_codec": audio.get("codec_name") if audio else None}
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("媒体信息读取失败") from None


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def thumbnail(path, kind, target, at=0.0):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if kind == "image":
        with Image.open(path) as source:
            img = ImageOps.exif_transpose(source).convert("RGB")
            img.thumbnail((960, 960))
            img.save(target, "JPEG", quality=88)
    elif kind == "video":
        run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(max(0, at)), "-i", path, "-frames:v", "1", "-vf", "scale=960:960:force_original_aspect_ratio=decrease", "-threads", "1", target], timeout=90)
    else:
        return None
    if not target.is_file() or not target.stat().st_size:
        raise ValueError("无法生成素材预览")
    return target


def scene_boundaries(path, duration):
    """Scene changes from decoded frames; no model or fabricated shot boundaries."""
    duration = float(duration)
    # A subject occupying only part of a static background can produce a real
    # cut with a score below 0.1. These remain candidate boundaries, not proof
    # that every visual change is a new editorial shot.
    result = run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", "-i", path, "-an", "-vf", "scale=320:-2,select='gt(scene,0.05)',showinfo", "-vsync", "vfr", "-f", "null", "-"], timeout=max(120, min(600, duration * 2)))
    import re
    candidates = [float(value) for value in re.findall(rb"pts_time:([0-9.]+)", result.stderr)]
    boundaries = [0.0]
    for at in candidates:
        if at - boundaries[-1] >= 0.4 and duration - at >= 0.3:
            boundaries.append(round(at, 3))
    boundaries.append(duration)
    return boundaries


def sample_frames(asset, directory, reference=False):
    path = Path(asset["path"])
    kind = asset["kind"]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if kind == "audio":
        return [], []
    if kind == "image":
        target = thumbnail(path, "image", directory / "frame-000.jpg")
        return [{"path": str(target), "time": 0.0}], []
    duration = float(asset["duration"])
    boundaries = scene_boundaries(path, duration) if reference else [0.0, duration]
    # Sampling grows with duration. Uniform sampling remains available even in a single long shot.
    count = min(32 if reference else 20, max(3, math.ceil(duration / 4) + 1))
    last_at = max(0.0, duration - 0.05)
    # Include both ends: dividing by count used to leave the entire tail
    # unsampled (e.g. a six-second video stopped at four seconds).
    timestamps = {round(i * last_at / (count - 1), 3) for i in range(count)}
    if reference:
        for left, right in zip(boundaries, boundaries[1:]):
            # Sample inside the detected shot, never precisely on its cut.
            timestamps.add(round(min(last_at, left + min(0.1, (right - left) / 4)), 3))
            timestamps.add(round(min(last_at, (left + right) / 2), 3))
    timestamps = sorted(max(0.0, value) for value in timestamps)
    maximum = 48 if reference else 20
    if len(timestamps) > maximum:
        timestamps = [timestamps[round(i * (len(timestamps) - 1) / (maximum - 1))] for i in range(maximum)]
    frames = []
    for index, at in enumerate(timestamps):
        target = thumbnail(path, "video", directory / f"frame-{index:03}.jpg", at)
        frames.append({"path": str(target), "time": at})
    return frames, boundaries
