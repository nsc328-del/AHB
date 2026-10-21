"""Local CPU speech recognition. Only public model weights are downloaded.

Media never leaves this process. The independent ASR queue lets local frame
extraction run concurrently; model analysis waits for this evidence to settle.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

SUPPORTED_MODELS = ("base", "small")
MODEL_LOCK = threading.Lock()
MODELS = {}


def _cache_root():
    return Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub").expanduser()


def cached_models():
    """Inspect only known public whisper cache locations; never scan the home tree."""
    root = _cache_root()
    result = []
    for model in SUPPORTED_MODELS:
        for owner in ("Systran", "guillaumekln"):
            snapshots = root / f"models--{owner}--faster-whisper-{model}" / "snapshots"
            if not snapshots.is_dir():
                continue
            for snapshot in sorted(snapshots.iterdir()):
                if snapshot.is_dir() and (snapshot / "model.bin").is_file() and (snapshot / "config.json").is_file():
                    result.append({"model": model, "path": str(snapshot.resolve())})
                    break
            if any(item["model"] == model for item in result):
                break
    return result


def status():
    available = cached_models()
    return {"engine": "faster-whisper", "device": "cpu", "models": list(SUPPORTED_MODELS), "cached_models": [item["model"] for item in available], "default_model": available[0]["model"] if available else "base", "media_upload": False}


def analysis_evidence(transcript):
    """Keep raw ASR for review; pass only confidence-screened speech to the model.

    These are conservative engineering thresholds, not calibrated probabilities
    of transcription accuracy. In particular, music can elicit plausible words.
    """
    policy = {"version": 1, "min_language_probability": .6, "min_mean_word_probability": .5}
    segments = (transcript or {}).get("segments") or []
    info = {"status": "no_speech", "reason": "未检出可用对白", "accepted_segment_count": 0,
            "excluded_segment_count": len(segments), "language_probability": (transcript or {}).get("language_probability"),
            "policy": policy}
    if not segments:
        return None, info
    probability = info["language_probability"]
    if probability is not None and float(probability) < policy["min_language_probability"]:
        info.update(status="uncertain", reason="语种识别置信度偏低，转写仅供核对")
        return None, info
    accepted = []
    for segment in segments:
        probabilities = [float(word["probability"]) for word in segment.get("words", []) if word.get("probability") is not None]
        # Old transcripts lacking confidence are not silently promoted to evidence.
        if not probabilities or sum(probabilities) / len(probabilities) < policy["min_mean_word_probability"]:
            continue
        if segment.get("no_speech_probability", 0) > .6 or segment.get("compression_ratio", 0) > 2.4:
            continue
        if str(segment.get("text", "")).strip():
            accepted.append(segment)
    excluded = len(segments) - len(accepted)
    info.update(accepted_segment_count=len(accepted), excluded_segment_count=excluded,
                status=("partial" if excluded else "used") if accepted else "uncertain",
                reason=("已融合通过置信度筛选的对白，部分转写待核对" if excluded else "已融合本地对白转写；自动识别仍可能有误") if accepted else "转写置信度偏低，未用于内容判断")
    if not accepted:
        return None, info
    return {**transcript, "segments": accepted, "text": " ".join(s["text"] for s in accepted),
            "evidence_policy": policy}, info


def transcribe(path, *, model=None, progress=None):
    emit = progress or (lambda **_: None)
    cached = cached_models()
    model = model or (cached[0]["model"] if cached else "base")
    if model not in SUPPORTED_MODELS:
        raise ValueError("本地转录支持 base 或 small 模型")
    path = Path(path)
    if not path.is_file():
        raise ValueError("原始音视频文件不存在")
    with MODEL_LOCK:
        if model not in MODELS:
            local = next((item["path"] for item in cached if item["model"] == model), None)
            if not local:
                emit(progress=3, message=f"首次准备 {model} 转录模型，仅下载公开权重，媒体留在本机", audio_status="downloading_model")
                try:
                    # Bound first-time downloads in a separate process. A stalled model
                    # host cannot monopolize the ASR queue indefinitely.
                    script = "import sys;from huggingface_hub import snapshot_download;print(snapshot_download(repo_id=sys.argv[1],token=False,etag_timeout=15,allow_patterns=['config.json','model.bin','tokenizer.json','vocabulary.*','preprocessor_config.json'],max_workers=2))"
                    environment = dict(os.environ, HF_HUB_DISABLE_XET="1", HF_HUB_DISABLE_PROGRESS_BARS="1", HF_HUB_DOWNLOAD_TIMEOUT="25")
                    downloaded = subprocess.run([sys.executable, "-c", script, f"Systran/faster-whisper-{model}"], env=environment, capture_output=True, text=True, timeout=120, check=True)
                    local = downloaded.stdout.strip().splitlines()[-1]
                    if not Path(local).is_dir():
                        raise ValueError("Missing downloaded model")
                except Exception:
                    raise RuntimeError("本地转录模型下载未完成，音频尚未转录；可以稍后重试，画面分析仍可使用") from None
            emit(progress=12, message=f"加载本地 {model} 转录模型", audio_status="running")
            try:
                from faster_whisper import WhisperModel
                MODELS[model] = WhisperModel(local, device="cpu", compute_type="int8", cpu_threads=min(4, os.cpu_count() or 2), num_workers=1, local_files_only=True)
            except Exception:
                raise RuntimeError("本地转录模型加载失败，音频尚未转录") from None
        emit(progress=15, message="在本机识别语音与时间点", audio_status="running")
        try:
            segments, info = MODELS[model].transcribe(str(path), beam_size=3, vad_filter=True, word_timestamps=True, condition_on_previous_text=False)
            output = []
            duration = float(info.duration or 0)
            for segment in segments:
                output.append({"start": round(float(segment.start), 3), "end": round(float(segment.end), 3), "text": segment.text.strip(),
                               "no_speech_probability": round(float(segment.no_speech_prob), 4),
                               "avg_log_probability": round(float(segment.avg_logprob), 4),
                               "compression_ratio": round(float(segment.compression_ratio), 4),
                               "words": [{"start": round(float(word.start), 3), "end": round(float(word.end), 3), "word": word.word, "probability": round(float(word.probability), 4)} for word in segment.words or []]})
                emit(progress=15 + min(80, 80 * segment.end / max(duration, .01)), message=f"已转录 {min(segment.end, duration):.0f} / {duration:.0f} 秒", audio_status="running")
        except Exception:
            raise RuntimeError("本地语音转录失败，未把音频内容当作已识别；可重试或补充准确文字稿") from None
    separator = "" if info.language in {"zh", "ja", "ko"} else " "
    return {"text": separator.join(item["text"] for item in output), "segments": output, "language": info.language, "language_probability": round(float(info.language_probability), 4), "duration": round(duration, 3), "model": model, "engine": "faster-whisper", "local": True, "has_speech": bool(output)}
