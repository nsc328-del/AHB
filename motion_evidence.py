"""Local image-motion evidence for a bounded interval, never a camera classifier."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np


LIMITS = [
    "光流描述画面上的位移，不等同于摄像机的物理运动；不能据此断言推近、变焦、横移轨道或速度曲线。",
    "外围区域只是背景候选，可能被人物、车辆、转场或遮挡占据；需与连续画面共同核对。",
    "低纹理、模糊、视差、曝光变化会降低跟踪可靠度；没有可靠跟踪不表示固定机位。",
    "已知切镜两侧不计算连续运动，转场由既有切镜证据单列。",
]


def _round(value, precision=5):
    return round(float(value), precision) if value is not None and math.isfinite(float(value)) else None


def _pair(previous, current, dt):
    height, width = previous.shape
    points = cv2.goodFeaturesToTrack(previous, maxCorners=240, qualityLevel=.015, minDistance=7, blockSize=7)
    if points is None or len(points) < 12:
        return {"qualified": False, "reason": "insufficient_features", "tracks": 0}
    moved, status, _ = cv2.calcOpticalFlowPyrLK(
        previous, current, points, None, winSize=(21, 21), maxLevel=3)
    if moved is None:
        return {"qualified": False, "reason": "tracking_failed", "tracks": 0}
    back, reverse_status, _ = cv2.calcOpticalFlowPyrLK(
        current, previous, moved, None, winSize=(21, 21), maxLevel=3)
    if back is None:
        return {"qualified": False, "reason": "reverse_tracking_failed", "tracks": 0}
    before, after = points.reshape(-1, 2), moved.reshape(-1, 2)
    good = ((status.ravel() == 1) & (reverse_status.ravel() == 1) &
            (np.linalg.norm(back.reshape(-1, 2) - before, axis=1) < 1.5) &
            (after[:, 0] >= 0) & (after[:, 0] < width) &
            (after[:, 1] >= 0) & (after[:, 1] < height))
    before, after = before[good], after[good]
    if len(before) < 12:
        return {"qualified": False, "reason": "few_consistent_tracks", "tracks": len(before)}
    edge = ((before[:, 0] < width*.25) | (before[:, 0] > width*.75) |
            (before[:, 1] < height*.2) | (before[:, 1] > height*.8))
    if edge.sum() < 10:
        return {"qualified": False, "reason": "few_background_candidates", "tracks": len(before)}
    affine, inliers = cv2.estimateAffinePartial2D(
        before[edge], after[edge], method=cv2.RANSAC, ransacReprojThreshold=1.8,
        maxIters=1000, confidence=.99, refineIters=10)
    if affine is None or inliers is None:
        return {"qualified": False, "reason": "inconsistent_background_candidates", "tracks": len(before)}
    selected = before[edge][inliers.ravel() == 1]
    ratio = float(inliers.mean())
    cells = {(int(x >= width/2), int(y >= height/2)) for x, y in selected}
    qualified = len(selected) >= 10 and ratio >= .55 and len(cells) >= 3
    predicted = np.column_stack((before, np.ones(len(before)))) @ affine.T
    residual = np.linalg.norm(after - predicted, axis=1)
    # Evaluate at the image center so an image rotation is not misreported as
    # origin-dependent translation. These remain pixel-space measurements.
    center = np.array([width/2, height/2, 1])
    displacement = affine @ center - center[:2]
    angle = math.degrees(math.atan2(affine[1, 0], affine[0, 0]))
    scale = math.hypot(affine[0, 0], affine[1, 0])
    return {
        "qualified": bool(qualified), "reason": "distributed_background_fit" if qualified else "weak_or_local_fit",
        "tracks": len(before), "background_candidate_tracks": int(edge.sum()),
        "background_inlier_ratio": _round(ratio), "background_quadrants": len(cells),
        "image_dx_widths_per_second": _round(displacement[0] / width / dt),
        "image_dy_heights_per_second": _round(displacement[1] / height / dt),
        "image_rotation_degrees_per_second": _round(angle / dt),
        "image_scale_log_per_second": _round(math.log(max(scale, 1e-8)) / dt),
        "unexplained_local_motion_widths_per_second_p75": _round(np.percentile(residual, 75) / width / dt),
        "center_residual_widths_per_second": _round(np.median(residual[~edge]) / width / dt) if (~edge).any() else None,
        "center_residual_widths_per_second_p90": _round(np.percentile(residual[~edge], 90) / width / dt) if (~edge).any() else None,
        "edge_residual_widths_per_second": _round(np.median(residual[edge]) / width / dt),
    }


def _aggregate(pairs):
    valid = [pair for pair in pairs if pair["qualified"]]
    result = {"measured_pairs": len(pairs), "qualified_pairs": len(valid),
              "qualified_pair_fraction": _round(len(valid) / len(pairs)) if pairs else 0}
    fields = ("image_dx_widths_per_second", "image_dy_heights_per_second",
              "image_rotation_degrees_per_second", "image_scale_log_per_second",
              "background_inlier_ratio", "unexplained_local_motion_widths_per_second_p75",
              "center_residual_widths_per_second", "center_residual_widths_per_second_p90",
              "edge_residual_widths_per_second")
    for key in fields:
        values = [pair[key] for pair in valid if pair.get(key) is not None]
        result[key + "_median"] = _round(np.median(values)) if values else None
    if valid:
        dx = np.array([p["image_dx_widths_per_second"] for p in valid])
        dy = np.array([p["image_dy_heights_per_second"] for p in valid])
        result["coherent_view_change_pair_fraction"] = _round(np.mean((abs(dx) >= .03) | (abs(dy) >= .03)))
        substantial = dx[abs(dx) >= .03]
        result["horizontal_sign_agreement"] = _round(max(np.mean(substantial > 0), np.mean(substantial < 0))) if len(substantial) else None
        result["image_horizontal_direction"] = "left" if np.median(dx) < -.03 else "right" if np.median(dx) > .03 else "mixed_or_small"
        result["interpretation"] = (
            "分布较广的背景候选存在连续同向画面位移；请结合图像判断取景变化，不直接命名物理运镜。"
            if result["coherent_view_change_pair_fraction"] >= .5 and result["qualified_pair_fraction"] >= .5 else
            "连续位移证据不充分或变化较小；不能据此认定固定机位。")
    else:
        result["interpretation"] = "缺少可靠的分布式背景跟踪；运镜不作自动判定。"
    return result


def _evidence_times(start, end):
    span = end-start
    left, right = start, max(start, end-min(.04, span/20))
    if span <= 3:
        return np.linspace(left, right, 9).tolist()
    middle = (left+right)/2
    # A one-second temporal sequence plus broad context, at most nine images.
    return sorted(set([left, left+span*.25, left+span*.75, right,
                       *[middle+offset for offset in (-.5, -.25, 0, .25, .5)]]))


def extract_motion_evidence(path, start, end, directory, *, cut_times=()):
    """Return <=9 timestamped images and adjacent-frame motion for <=60 seconds.

    Frames keep the source video's absolute presentation times. Flow runs on
    every decoded neighboring pair within the requested interval, skipping
    pairs crossing known cuts. This function never calls a model or edits media.
    """
    start, end = float(start), float(end)
    if not math.isfinite(start+end) or start < 0 or not 0 < end-start <= 60:
        raise ValueError("运动核对需要原片内 0–60 秒的有效选段")
    path, directory = Path(path).resolve(), Path(directory).resolve()
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("无法解码运动核对素材")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if not math.isfinite(fps) or fps <= 0 or end > count/fps + .05:
        capture.release()
        raise ValueError("运动核对时间超出视频或帧率无效")
    directory.mkdir(parents=True, exist_ok=True)
    wanted = _evidence_times(start, end)
    frame_index = int(math.floor(start*fps))
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    previous, previous_time, previous_at = None, None, -1
    pairs, frames, skipped_cuts = [], [], []
    decoded, target = 0, 0
    relevant_cuts = sorted(float(t) for t in cut_times if start < float(t) < end)
    try:
        while True:
            ok, original = capture.read()
            if not ok:
                break
            at = float(capture.get(cv2.CAP_PROP_POS_MSEC))/1000
            if not math.isfinite(at) or at <= previous_at or (at < start-1/fps and frame_index > 0):
                at = frame_index/fps
            frame_index += 1
            previous_at = at
            if at + .0001 < start:
                continue
            if at >= end - .0001:
                break
            height, width = original.shape[:2]
            scale = min(1, 480/max(height, width))
            small = cv2.resize(original, (round(width*scale), round(height*scale)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            decoded += 1
            if previous is not None:
                cuts = [t for t in relevant_cuts if previous_time+.0005 < t <= at+.0005]
                if cuts:
                    skipped_cuts.extend(cuts)
                else:
                    pairs.append({"start": _round(previous_time, 6), "end": _round(at, 6),
                                  **_pair(previous, gray, at-previous_time)})
            if target < len(wanted) and at+.0001 >= wanted[target]:
                output = directory / f"motion-{len(frames):02d}.jpg"
                resize = min(1, 960/max(height, width))
                saved = cv2.resize(original, (round(width*resize), round(height*resize)))
                if not cv2.imwrite(str(output), saved, [cv2.IMWRITE_JPEG_QUALITY, 89]):
                    raise ValueError("无法保存运动核对帧")
                frames.append({"path": str(output), "time": _round(at, 6),
                               "sequence": len(frames), "purpose": "temporal_motion_evidence"})
                while target < len(wanted) and wanted[target] <= at+.0001:
                    target += 1
            previous, previous_time = gray, at
    finally:
        capture.release()
    if decoded < 2:
        raise ValueError("选段中没有足够连续帧")
    return {"version": 1, "method": "adjacent_lk_background_candidate_ransac",
            "start": start, "end": end, "fps": _round(fps, 6), "decoded_frames": decoded,
            "frames": frames, "statistics": _aggregate(pairs), "pair_evidence": pairs,
            "known_cuts_inside": relevant_cuts, "skipped_cut_pairs": sorted(set(skipped_cuts)),
            "limits": LIMITS, "model_calls": 0}
