"""Local timing evidence, not a music/speech classifier or a beat ground truth."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy import ndimage, signal

SAMPLE_RATE = 16000
HOP = 160
FFT_SIZE = 512
STEP = HOP / SAMPLE_RATE
MATCH_TOLERANCE = .10
MATCH_EPSILON = 1e-9
BASELINE_SHIFTS = (-.5, -.35, -.2, .2, .35, .5)
LIMITS = [
    "起音是频谱突变候选，可能来自鼓点、语音辅音、音效、剪接或噪声，不等同于音乐节拍。",
    "没有执行音乐/语音分离或声源分类；不凭能量、起音或周期性将片段标为配乐或说话。",
    "beat_candidates 是获得局部起音支持的周期性脉冲候选，不是人工确认的音乐节拍。",
    "bpm 表示稳定局部脉冲的重复速率；音乐拍号、强拍位置及半拍/倍拍解释仍未确认。",
    "低置信窗口的 bpm 为 null；节奏可以变化，不用一个全片 BPM 外推，也不在无证据位置补拍。",
    "cut_offset_seconds = 切镜时间 - 音频事件时间；正数表示切镜发生在事件之后。",
    "切镜接近起音或候选拍点只说明时间接近；密集音频事件会偶然命中，不能据此断言刻意卡点。",
]


def _round(value, digits=4):
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def _run(args, timeout=120):
    try:
        result = subprocess.run([str(v) for v in args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("音频节奏证据提取失败或超时") from None
    if result.returncode:
        raise ValueError("无法解码音频节奏证据")
    return result.stdout


def _decode(path):
    metadata = json.loads(_run([
        shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_format",
        "-show_streams", "-of", "json", path,
    ], timeout=45))
    streams = metadata.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    info = metadata.get("format", {})
    duration = float(info.get("duration") or (audio or {}).get("duration") or 0)
    origin = float(info.get("start_time") or 0)
    if not math.isfinite(duration) or duration < 0 or duration > 7200:
        raise ValueError("节奏分析支持两小时以内的媒体")
    timing = {
        "time_basis": "original_media_playback_seconds",
        "container_start_time": _round(origin),
        "audio_stream_start_time": _round(float((audio or {}).get("start_time") or origin)),
        "normalization": "ffmpeg copyts/start_at_zero; resample first_pts=0 preserves audio offset and gaps",
    }
    if not audio:
        return None, duration, timing
    # Preserve the audio stream's offset relative to the container playback
    # origin. Simply decoding to a fresh WAV would discard a delayed track.
    pcm = _run([
        shutil.which("ffmpeg") or "ffmpeg", "-nostdin", "-hide_banner",
        "-loglevel", "error", "-copyts", "-start_at_zero", "-i", path,
        "-map", "0:a:0", "-vn", "-af",
        f"aresample={SAMPLE_RATE}:async=1:first_pts=0", "-ac", "1",
        "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1",
    ])
    samples = np.frombuffer(pcm, dtype="<f4")
    if not len(samples) or not np.isfinite(samples).all():
        raise ValueError("音频解码结果为空或无效")
    return samples, max(duration, len(samples) / SAMPLE_RATE), timing


def _features(samples):
    # Centered STFT: returned t is the center of each analysis window. No
    # additional half-window offset is silently added to an onset timestamp.
    transform_samples = samples if len(samples) >= FFT_SIZE else np.pad(samples, (0, FFT_SIZE-len(samples)))
    frequencies, times, spectrum = signal.stft(
        transform_samples, fs=SAMPLE_RATE, window="hann", nperseg=FFT_SIZE,
        noverlap=FFT_SIZE-HOP, boundary="zeros", padded=True,
    )
    valid = times < len(samples) / SAMPLE_RATE
    times, magnitude = times[valid], np.abs(spectrum[:, valid])
    # Log-frequency bands prevent numerous high-frequency FFT bins from
    # dominating the onset envelope. Flux and RMS remain separate signals.
    edges = np.geomspace(45, SAMPLE_RATE/2, 33)
    bands = []
    for left, right in zip(edges[:-1], edges[1:]):
        selected = (frequencies >= left) & (frequencies < right)
        if selected.any():
            bands.append(np.sqrt(np.mean(magnitude[selected]**2, axis=0)))
    compressed = np.log1p(100 * np.asarray(bands))
    positive = np.maximum(np.diff(compressed, axis=1, prepend=compressed[:, :1]), 0)
    flux = np.mean(positive, axis=0)
    # Adaptive threshold preserves quiet transients without interpreting
    # continuous loudness as a beat. Floor rejects near-silence numerical noise.
    median_size = min(101, len(flux) if len(flux) % 2 else len(flux)-1)
    local_median = ndimage.median_filter(flux, size=max(1, median_size), mode="nearest")
    deviation = ndimage.median_filter(np.abs(flux-local_median), size=max(1, median_size), mode="nearest")
    absolute_floor = max(float(np.percentile(flux, 95)) * .08, 1e-5)
    threshold = local_median + 2.5*deviation + absolute_floor
    novelty = np.maximum(flux-local_median, 0)
    if len(flux) >= 3:
        peak_indices, properties = signal.find_peaks(
            flux, height=threshold, distance=7, prominence=absolute_floor, wlen=101,
        )
    else:
        peak_indices, properties = np.array([], dtype=int), {"prominences": np.array([])}
    power = ndimage.uniform_filter1d(samples.astype(np.float64)**2, size=FFT_SIZE, mode="constant")
    center_indices = np.minimum((times*SAMPLE_RATE).astype(int), len(samples)-1)
    rms_db = 10*np.log10(np.maximum(power[center_indices], 1e-12))
    keep = rms_db[peak_indices] > -65
    peak_indices = peak_indices[keep]
    prominence = properties["prominences"][keep]
    scale = max(float(np.percentile(novelty[peak_indices], 90)) if len(peak_indices) else 0, 1e-8)
    onsets = [{
        "time": _round(times[index]), "strength": _round(min(2, novelty[index]/scale)),
        "spectral_flux": _round(flux[index], 7),
        "prominence": _round(prominence[i], 7), "energy_dbfs": _round(rms_db[index], 2),
    } for i, index in enumerate(peak_indices)]
    return times, rms_db, flux, novelty, onsets


def _nearest(values, queries):
    """Nearest sorted value and signed query-minus-value distance."""
    values, queries = np.asarray(values), np.asarray(queries)
    if not len(values):
        return np.full(len(queries), -1, int), np.full(len(queries), np.inf)
    right = np.searchsorted(values, queries).clip(0, len(values)-1)
    left = (right-1).clip(0, len(values)-1)
    indices = np.where(abs(queries-values[left]) <= abs(queries-values[right]), left, right)
    return indices, queries-values[indices]


def _pulse_fit(period, onset_times, weights, start, end):
    tolerance = min(.065, period*.14)
    best = None
    for phase in np.arange(0, period, STEP):
        grid = np.arange(start+phase, end, period)
        if len(grid) < 5:
            continue
        nearest, distances = _nearest(onset_times, grid)
        supported = abs(distances) <= tolerance
        support = float(np.mean(supported))
        event_distance = abs(((onset_times-(start+phase)+period/2) % period)-period/2)
        mass = float(weights[event_distance <= tolerance].sum() / max(weights.sum(), 1e-9))
        fscore = 2*support*mass / max(support+mass, 1e-9)
        score = fscore - float(np.mean(np.minimum(abs(distances), tolerance))) * .25
        if best is None or score > best[0]:
            best = (score, phase, grid, nearest, distances, supported)
    if best is None:
        return None
    _, phase, grid, nearest, distances, supported = best
    # Refine interval/phase from actual supporting onsets, not all synthesized
    # grid points. This avoids accumulating the 10 ms lag quantization error.
    if supported.sum() >= 5:
        beat_indices = np.arange(len(grid))[supported]
        slope, intercept = np.polyfit(beat_indices, onset_times[nearest[supported]], 1)
        if abs(slope-period) <= period*.035:
            period = float(slope)
            phase = float((intercept-start) % period)
            grid = np.arange(start+phase, end, period)
            nearest, distances = _nearest(onset_times, grid)
            tolerance = min(.065, period*.14)
            supported = abs(distances) <= tolerance
    support = float(np.mean(supported)) if len(grid) else 0
    event_distance = abs(((onset_times-(start+phase)+period/2) % period)-period/2)
    mass = float(weights[event_distance <= tolerance].sum() / max(weights.sum(), 1e-9))
    jitter = float(np.median(abs(distances[supported]))) if supported.any() else 1
    return {
        "period": period, "phase": phase, "grid": grid, "nearest": nearest,
        "supported": supported, "support": support, "mass": mass, "jitter": jitter,
        "fit": 2*support*mass/max(support+mass, 1e-9),
    }


def _window(start, end, times, novelty, onsets):
    rows = [row for row in onsets if start <= row["time"] < end]
    base = {"start": _round(start), "end": _round(end), "bpm": None,
            "confidence": "insufficient", "score": 0, "onset_count": len(rows),
            "sound_source_type": "unclassified", "alternatives": [], "beat_candidates": []}
    if end-start < 5 or len(rows) < 6:
        return {**base, "reason": "too_few_onsets_or_short_interval"}
    envelope = novelty[(times >= start) & (times < end)].astype(np.float64)
    envelope = np.maximum(envelope - np.percentile(envelope, 40), 0)
    # Winsorize outliers: one crash/slam must not determine a whole window.
    nonzero = envelope[envelope > 1e-8]
    envelope = np.minimum(envelope, max(np.percentile(nonzero, 97), 1e-8) if len(nonzero) else 1e-8)
    centered = envelope-envelope.mean()
    norm = np.linalg.norm(centered)
    if norm < 1e-8:
        return {**base, "reason": "no_periodicity_evidence"}
    ac = signal.correlate(centered, centered, mode="full", method="fft")[len(centered)-1:]
    for lag in range(1, min(160, len(ac))):
        denominator = np.linalg.norm(centered[:-lag])*np.linalg.norm(centered[lag:])
        ac[lag] = ac[lag]/max(denominator, 1e-12)
    low, high = math.ceil(60/210/STEP), min(math.floor(60/45/STEP), len(ac)-2)
    peaks, _ = signal.find_peaks(ac[low:high+1], prominence=.015)
    lags = sorted((int(index)+low for index in peaks), key=lambda lag: ac[lag], reverse=True)[:8]
    event_times = np.array([r["time"] for r in rows])
    weights = np.array([max(.05, min(1, r["strength"])) for r in rows])
    candidates = []
    for lag in lags:
        if ac[lag] < .12:
            continue
        denominator = ac[lag-1]-2*ac[lag]+ac[lag+1]
        delta = .5*(ac[lag-1]-ac[lag+1])/denominator if abs(denominator) > 1e-9 else 0
        period = (lag+np.clip(delta, -.5, .5))*STEP
        fitted = _pulse_fit(period, event_times, weights, start, end)
        if fitted is None:
            continue
        fitted["autocorrelation"] = float(ac[lag])
        fitted["score"] = float(fitted["fit"] * (.55+.45*max(0, ac[lag])) *
                                max(.3, 1-fitted["jitter"]/.1))
        candidates.append(fitted)
    if not candidates:
        return {**base, "reason": "no_supported_periodic_pulse"}
    candidates.sort(key=lambda row: row["score"], reverse=True)
    best = candidates[0]
    # Preserve octave alternatives; do not silently claim a metrical beat.
    alternatives = [{"bpm": _round(60/c["period"], 2), "score": _round(c["score"]),
                     "autocorrelation": _round(c["autocorrelation"]),
                     "grid_support": _round(c["support"]), "onset_mass_supported": _round(c["mass"])}
                    for c in candidates[:3]]
    strong = (best["autocorrelation"] >= .2 and best["support"] >= .70 and
              best["mass"] >= .45 and best["jitter"] <= .035 and
              best["supported"].sum() >= 6 and best["score"] >= .47)
    if len(candidates) > 1:
        other = candidates[1]
        ratio = best["period"]/other["period"]
        ambiguous = (other["score"] >= best["score"]*.94 and
                     not .97 <= ratio <= 1.03)
    else:
        ambiguous = False
    confident = strong and not ambiguous
    beats = []
    if confident:
        for predicted, index, supported in zip(best["grid"], best["nearest"], best["supported"]):
            if supported:
                beats.append({
                    "time": _round(event_times[index]), "grid_time": _round(predicted),
                    "grid_offset_seconds": _round(event_times[index]-predicted),
                    "onset_strength": rows[index]["strength"],
                })
    return {
        **base, "bpm": _round(60/best["period"], 2) if confident else None,
        "confidence": "supported_local_pulse" if confident else "uncertain",
        "score": _round(best["score"]), "alternatives": alternatives,
        "median_grid_error_seconds": _round(best["jitter"]),
        "reason": "periodic_onset_support" if confident else "ambiguous_period" if ambiguous else "weak_periodic_support",
        "beat_candidates": beats,
    }


def _coverage(events, duration, tolerance):
    intervals = sorted((max(0, t-tolerance), min(duration, t+tolerance)) for t in events)
    covered, right = 0, 0
    for start, end in intervals:
        covered += max(0, end-max(start, right))
        right = max(right, end)
    return covered/duration if duration else 0


def _match_cuts(cuts, duration, onsets, beats):
    cuts = sorted(set(float(t) for t in cuts))
    if any(not math.isfinite(t) or t < 0 or t > duration+.1 for t in cuts):
        raise ValueError("切镜时间超出媒体范围")
    # Whole-film endpoints are not edit events.
    internal = [t for t in cuts if t > .05 and t < duration-.05]
    results = []
    for at in internal:
        row = {"cut_time": _round(at)}
        for key, events in (("onset", onsets), ("beat_candidate", beats)):
            if events:
                index, offset = _nearest([e["time"] for e in events], [at])
                event = events[index[0]]
                row[key] = {
                    "time": event["time"], "cut_offset_seconds": _round(offset[0]),
                    "offset_ms": _round(offset[0]*1000, 1),
                    "within_tolerance": bool(abs(offset[0]) <= MATCH_TOLERANCE+MATCH_EPSILON),
                    "strength": event.get("strength", event.get("onset_strength")),
                }
            else:
                row[key] = None
        results.append(row)
    summary = {"cut_count": len(internal), "excluded_boundary_count": len(cuts)-len(internal),
               "tolerance_seconds": MATCH_TOLERANCE, "numeric_epsilon_seconds": MATCH_EPSILON}
    for name, events in (("onset", onsets), ("beat_candidate", beats)):
        matched = sum(bool(row[name] and row[name]["within_tolerance"]) for row in results)
        summary[name] = {
            "matched_cuts": matched, "cut_fraction": _round(matched/len(internal)) if internal else None,
            "uniform_time_coverage_fraction": _round(_coverage([e["time"] for e in events], duration, MATCH_TOLERANCE)),
        }
    baseline = []
    for shift in BASELINE_SHIFTS:
        shifted = [t+shift for t in internal if .05 < t+shift < duration-.05]
        entry = {"shift_seconds": shift, "cut_count": len(shifted)}
        for name, events in (("onset", onsets), ("beat_candidate", beats)):
            _, distances = _nearest([e["time"] for e in events], shifted)
            count = int(np.sum(abs(distances) <= MATCH_TOLERANCE+MATCH_EPSILON))
            entry[name+"_matched_cuts"] = count
            entry[name+"_fraction"] = _round(count/len(shifted)) if shifted else None
        baseline.append(entry)
    summary["shifted_cut_baseline"] = baseline
    summary["baseline_interpretation"] = "相同切点整体平移后的邻近命中率，只作偶合参照，不是显著性检验或刻意卡点的证明。"
    for name in ("onset", "beat_candidate"):
        values = [row[name+"_fraction"] for row in baseline if row[name+"_fraction"] is not None]
        summary[name]["shifted_baseline_mean_fraction"] = _round(np.mean(values)) if values else None
    return results, summary


def analyze_audio_rhythm(path, directory, cut_times=()):
    """Extract original-timeline local evidence and persist JSON/10 ms features.

    No model calls and no source-class inference. Returned JSON can be scoped
    with rhythm_for_interval before being supplied to a video analysis model.
    """
    path, directory = Path(path), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    samples, duration, timing = _decode(path)
    result = {
        "version": 1, "method": "spectral_flux_local_periodicity_v1",
        "duration": _round(duration), "timing": timing,
        "sample_rate": SAMPLE_RATE, "analysis_hop_seconds": STEP,
        "sound_source_type": "unclassified", "model_calls": 0,
        "limits": LIMITS, "status": "no_audio" if samples is None else "analyzed",
        "onsets": [], "local_windows": [], "beat_candidates": [], "energy_by_second": [],
    }
    if samples is not None:
        times, energy, flux, novelty, onsets = _features(samples)
        audio_end = len(samples)/SAMPLE_RATE
        starts = list(np.arange(0, max(0, audio_end-12)+.001, 4))
        if audio_end > 12 and audio_end-(starts[-1]+12) > .5:
            starts.append(audio_end-12)
        windows = [_window(float(start), min(float(start)+12, audio_end), times, novelty, onsets)
                   for start in starts]
        # Keep observed onsets only, never an unsupported metronomic grid.
        beat_index = {}
        for index, window in enumerate(windows):
            for beat in window["beat_candidates"]:
                item = beat_index.setdefault(beat["time"], {**beat, "supporting_windows": []})
                item["supporting_windows"].append(index)
        beats = sorted(beat_index.values(), key=lambda item: item["time"])
        energies = []
        for second in range(math.ceil(audio_end)):
            local = energy[(times >= second) & (times < second+1)]
            if len(local):
                energies.append({"start": second, "end": _round(min(audio_end, second+1)),
                                 "mean_dbfs": _round(10*np.log10(np.mean(10**(local/10))), 2),
                                 "peak_window_dbfs": _round(max(local), 2)})
        result.update(onsets=onsets, local_windows=windows, beat_candidates=beats, energy_by_second=energies,
                      audio_end=_round(audio_end))
        np.savez_compressed(directory/"rhythm-features.npz", time=times, energy_dbfs=energy,
                            spectral_flux=flux, novelty=novelty)
    matches, alignment = _match_cuts(cut_times, duration, result["onsets"], result["beat_candidates"])
    result.update(cut_matches=matches, alignment_summary=alignment,
                  summary={"onset_count": len(result["onsets"]), "beat_candidate_count": len(result["beat_candidates"]),
                           "supported_local_windows": sum(w["bpm"] is not None for w in result["local_windows"]),
                           "window_count": len(result["local_windows"]), "global_bpm": None})
    (directory/"rhythm-evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return result


def rhythm_for_interval(evidence, start, end):
    """Keep absolute times; do not send neighboring intervals' events to DS."""
    start, end = float(start), float(end)
    if not math.isfinite(start+end) or not 0 <= start < end:
        raise ValueError("节奏证据时间范围无效")
    windows = []
    for window in evidence.get("local_windows", []):
        if window["end"] <= start or window["start"] >= end:
            continue
        # Window's period fit uses broader context, explicitly distinguished
        # from the narrowly scoped actual events returned below.
        windows.append({key: value for key, value in window.items() if key != "beat_candidates"})
    return {
        "version": evidence["version"], "method": evidence["method"], "status": evidence["status"],
        "start": start, "end": end, "sound_source_type": "unclassified",
        "local_periodicity_context": windows,
        "onsets": [row for row in evidence.get("onsets", []) if start <= row["time"] < end],
        "beat_candidates": [row for row in evidence.get("beat_candidates", []) if start <= row["time"] < end],
        "cut_matches": [row for row in evidence.get("cut_matches", []) if start <= row["cut_time"] < end],
        "tolerance_seconds": MATCH_TOLERANCE,
        "full_asset_alignment_baseline": evidence.get("alignment_summary"),
        "limits": evidence["limits"],
    }
