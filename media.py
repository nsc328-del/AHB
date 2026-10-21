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


def _visual_difference(before, after):
    """Compare decoded RGB pixels after compensating a coherent camera pan."""
    import cv2
    import numpy as np

    a, gray_a, hist_a = before
    b, gray_b, hist_b = after
    raw = float(np.mean(np.abs(b - a))) / 255
    (dx_float, dy_float), response = cv2.phaseCorrelate(gray_a, gray_b)
    dx, dy = round(dx_float), round(dy_float)
    compensated = raw
    height, width = gray_a.shape
    if response > .15 and abs(dx) < width * .3 and abs(dy) < height * .3:
        x1, x2 = max(0, dx), min(width, width + dx)
        y1, y2 = max(0, dy), min(height, height + dy)
        compensated = min(raw, float(np.mean(np.abs(
            b[y1:y2, x1:x2] - a[y1-dy:y2-dy, x1-dx:x2-dx]
        ))) / 255)
    ac, bc = gray_a - gray_a.mean(), gray_b - gray_b.mean()
    denominator = float(np.linalg.norm(ac) * np.linalg.norm(bc))
    correlation = float(np.sum(ac * bc)) / denominator if denominator > 1 else 0.0
    return {
        "raw": raw, "difference": compensated,
        "histogram": float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA)),
        "motion_response": float(response), "correlation": correlation,
        "motion_reduction": compensated / max(raw, .0001),
        "brightness_change": abs(float(gray_a.mean() - gray_b.mean())) / 255,
    }


def _frame_features(frame):
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    scale = 256 / max(height, width)
    pixels = cv2.resize(frame, (max(16, round(width * scale)), max(16, round(height * scale))))
    hsv = cv2.cvtColor(pixels, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [32, 8], [0, 180, 0, 256])
    cv2.normalize(histogram, histogram, alpha=1, norm_type=cv2.NORM_L1)
    return pixels.astype(np.float32), cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY).astype(np.float32), histogram


def scene_detection(path, duration):
    """Local hard-cut detection plus explicitly unconfirmed gradual transitions.

    Frame differences alone mistake pans, flashes and fades for cuts. We align
    translational motion and require an isolated temporal jump. A slow visual
    change is retained as a review interval rather than an invented exact cut.
    """
    import cv2
    import numpy as np
    from collections import deque

    duration = float(duration)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("无法解码视频进行切镜识别")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not math.isfinite(fps) or fps <= 0:
        capture.release()
        raise ValueError("无法读取视频帧率")
    previous = None
    metrics, slow_candidates = [], []
    recent = deque()
    last_slow_at = -1.0
    last_at = -1.0
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            at = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
            if not math.isfinite(at) or at <= last_at:
                at = frame_index / fps
            features = _frame_features(frame)
            if previous is not None:
                metrics.append({"time": at, **_visual_difference(previous, features)})
            if at - last_slow_at >= .24:
                while recent and at - recent[0][0] > 1:
                    recent.popleft()
                if recent and at - recent[0][0] >= .55:
                    distance = _visual_difference(recent[0][1], features)
                    recent_motion = metrics[recent[0][2]:]
                    is_pan = bool(recent_motion) and float(np.median([m["motion_reduction"] for m in recent_motion])) < .6
                    if not is_pan and distance["difference"] >= .065 and (
                        distance["histogram"] >= .20 or distance["brightness_change"] >= .06
                    ):
                        slow_candidates.append({"start": recent[0][0], "end": at,
                                                "difference": distance["difference"]})
                recent.append((at, features, len(metrics)))
                last_slow_at = at
            previous, last_at = features, at
            frame_index += 1
    finally:
        capture.release()
    if frame_index < 1:
        raise ValueError("视频中没有可解码的画面")

    differences = np.array([item["difference"] for item in metrics])
    radius = max(3, round(fps * .3))
    cuts, review_candidates = [], []
    for index, item in enumerate(metrics):
        difference = item["difference"]
        nearby = np.concatenate((differences[max(0, index-radius):max(0, index-1)],
                                 differences[index+2:index+radius+1]))
        baseline = float(np.median(nearby)) if len(nearby) else 0.0
        relative = difference / max(baseline, .008)
        if difference < .025 or relative < 1.6:
            continue
        if difference < max(differences[max(0, index-1):index+2], default=0):
            continue
        adjacent = max(differences[index-1] if index else 0,
                       differences[index+1] if index+1 < len(metrics) else 0, .005)
        abruptness = difference / adjacent
        visual_jump = ((relative >= 3.2 and item["histogram"] >= .12)
                       or (difference >= .12 and item["histogram"] >= .2)
                       or (difference >= .12 and relative >= 4 and item["motion_response"] < .15 and item["correlation"] < .5))
        isolated = abruptness >= 2.1 or (item["histogram"] >= .7 and item["motion_response"] < .2)
        stable_geometry = item["correlation"] > .87 and item["motion_response"] > .2 and item["histogram"] < .6
        fade = item["motion_response"] > .8 and item["histogram"] < .5
        accepted = visual_jump and isolated and not stable_geometry and not fade
        if item["time"] < 1 / fps or duration - item["time"] < 1 / fps:
            continue
        record = {
            "time": round(item["time"], 3), "type": "hard_cut", "transition": "hard_cut",
            "difference": round(difference, 4), "histogram_difference": round(item["histogram"], 4),
            "temporal_ratio": round(relative, 2), "abruptness": round(abruptness, 2),
            "confidence": "high" if abruptness >= 2.5 and relative >= 4 else "moderate",
        }
        if accepted:
            cuts.append(record)
        elif visual_jump:
            review_candidates.append({**record, "type": "visual_change", "needs_review": True})

    # Remove slow changes that merely span an already located hard cut. The
    # remaining intervals may be dissolves, fades, or substantial scene motion;
    # they are evidence for extra sampling, never promoted to verified cuts.
    gradual = []
    for item in slow_candidates:
        if any(item["start"] - .1 <= cut["time"] <= item["end"] + .1 for cut in cuts):
            continue
        if gradual and item["start"] <= gradual[-1]["end"] + .3 and not any(
            gradual[-1]["end"] < cut["time"] < item["start"] for cut in cuts
        ):
            gradual[-1]["end"] = round(item["end"], 3)
        else:
            gradual.append({"start": round(item["start"], 3), "end": round(item["end"], 3),
                            "type": "gradual", "confidence": "candidate", "needs_review": True})
    boundaries = [0.0, *[cut["time"] for cut in cuts], duration]
    return {
        "method": "motion_compensated_temporal_difference_v2", "fps": round(fps, 6),
        "decoded_frames": frame_index, "boundaries": boundaries, "cuts": cuts,
        "gradual_transitions": gradual, "review_candidates": review_candidates,
        "coverage": "hard_cuts_with_gradual_candidates",
        "limitations": ["渐变区间为待核对候选，可能包含运镜或物体运动；不计入确定切点。",
                        "强烈运动、闪光和相似构图切换仍可能漏检或误检，镜头边界支持人工核对。"],
    }


def scene_boundaries(path, duration):
    return scene_detection(path, duration)["boundaries"]


def _sample_video_frames(path, timestamps, directory):
    """Decode once, recording actual presentation times rather than seek labels."""
    import cv2

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("无法解码视频抽帧")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    frames = []
    target_index = 0
    frame_index = 0
    last_at = -1.0
    try:
        while target_index < len(timestamps):
            ok, frame = capture.read()
            if not ok:
                break
            at = float(capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
            if not math.isfinite(at) or at <= last_at:
                at = frame_index / fps
            if at + .0001 >= timestamps[target_index]:
                height, width = frame.shape[:2]
                if max(height, width) > 960:
                    scale = 960 / max(height, width)
                    frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
                target = directory / f"frame-{len(frames):03}.jpg"
                if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 88]):
                    raise ValueError("无法保存视频证据帧")
                frames.append({"path": str(target), "time": round(at, 3)})
                while target_index < len(timestamps) and timestamps[target_index] <= at + .0001:
                    target_index += 1
            frame_index += 1
            last_at = at
    finally:
        capture.release()
    if not frames:
        raise ValueError("无法生成视频证据帧")
    return frames


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
    detection = scene_detection(path, duration) if reference else None
    boundaries = detection["boundaries"] if detection else [0.0, duration]
    if detection:
        (directory / "detection.json").write_text(json.dumps(detection, ensure_ascii=False, indent=2))
    last_at = max(0.0, duration - .05)
    count = max(3, math.ceil(duration / 4) + 1)
    if not reference:
        count = min(20, count)
    timestamps = {round(i * last_at / (count - 1), 3) for i in range(count)}
    if reference:
        for left, right in zip(boundaries, boundaries[1:]):
            inset = min(.07, (right - left) / 4)
            timestamps.update((round(min(last_at, left + inset), 3),
                               round(min(last_at, (left + right) / 2), 3),
                               round(min(last_at, right - inset), 3)))
        for transition in detection["gradual_transitions"]:
            # A two-second title can disappear between regular four-second
            # evidence frames. Keep dense evidence throughout slow transitions.
            left = max(0.0, transition["start"] - .5)
            right = min(last_at, transition["end"] + .5)
            steps = max(1, math.ceil(right - left))
            timestamps.update(round(left + i * (right-left) / steps, 3) for i in range(steps+1))
    timestamps = sorted(max(0.0, value) for value in timestamps)
    return _sample_video_frames(path, timestamps, directory), boundaries
