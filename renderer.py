"""Deterministic local FFmpeg renderer for the workbench's editable plans.

``transition=fade`` means a 250 ms fade from black at the start of that clip.
It never overlaps adjacent clips, so the plan and rendered timeline agree.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unicodedata
from typing import Callable


FPS = 30
SAMPLE_RATE = 48000


class RenderError(ValueError):
    """A plan cannot be rendered, or its output failed verification."""


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"媒体处理未完成：{exc}") from exc
    if result.returncode:
        raise RenderError(f"媒体处理失败：{result.stderr[-4000:].strip()}")
    return result


def _probe(path: Path, ffprobe: str) -> dict:
    result = _run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout=60)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RenderError("无法读取素材信息") from exc
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    duration = data.get("format", {}).get("duration") or (video or audio or {}).get("duration") or 0
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = 0
    try:
        video_duration = float((video or {}).get("duration") or duration)
    except (TypeError, ValueError):
        video_duration = duration
    return {"duration": duration, "video_duration": video_duration, "width": (video or {}).get("width", 0),
            "height": (video or {}).get("height", 0), "video": video, "audio": audio}


def _ffmpeg_binary() -> tuple[str, bool]:
    configured = os.environ.get("FFMPEG")
    candidates = [shutil.which(configured)] if configured else [shutil.which("ffmpeg")]
    if not configured:
        try:
            import imageio_ffmpeg
            candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
        except (ImportError, RuntimeError):
            pass
    available = []
    for candidate in dict.fromkeys(path for path in candidates if path):
        filters = _run([candidate, "-hide_banner", "-filters"], timeout=20).stdout
        has_ass = bool(re.search(r"\s+ass\s+", filters))
        available.append((candidate, has_ass))
        if has_ass:
            return candidate, True
    if not available:
        raise RenderError("找不到 FFmpeg，请安装媒体处理依赖")
    return available[0]


def _number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise RenderError(f"{label}必须是数字")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RenderError(f"{label}必须是数字") from exc
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        raise RenderError(f"{label}必须是有限{'正数' if positive else '非负数'}")
    return result


def _dimension(value: object, label: str) -> int:
    result = _number(value, label, positive=True)
    if not result.is_integer() or result < 64 or result > 4096 or int(result) % 2:
        raise RenderError(f"{label}须为64到4096之间的偶数")
    return int(result)


def _ass_time(seconds: float) -> str:
    centiseconds = round(seconds * 100)
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{fraction:02d}"


def _caption_lines(text: str, columns: int) -> str:
    # ASS has no reliable escaping for literal override syntax; full-width
    # punctuation preserves its visible meaning without interpreting commands.
    text = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc")
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line, width = "", 0
        for char in paragraph:
            cost = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
            if line and width + cost > columns:
                lines.append(line.rstrip())
                line, width = "", 0
            line += " " if char == "\t" else char
            width += cost
        lines.append(line.rstrip())
    return "\\N".join(lines)


def _ass_document(clips: list[dict], width: int, height: int, font: str) -> str:
    font_size = max(20, round(min(width, height) * .05))
    margin = max(16, round(width * .07))
    vertical_margin = max(24, round(height * .065))
    columns = max(12, int((width - margin * 2) / (font_size * .53)))
    font = font.replace(",", " ").replace("\n", " ").replace("\r", " ")
    header = textwrap.dedent(f"""\
        [Script Info]
        ScriptType: v4.00+
        PlayResX: {width}
        PlayResY: {height}
        WrapStyle: 2
        ScaledBorderAndShadow: yes

        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: Caption,{font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H70000000,-1,0,0,0,100,100,0,0,1,2,1,2,{margin},{margin},{vertical_margin},1

        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        """)
    events = []
    for clip in clips:
        caption = str(clip.get("caption") or "").strip()
        if caption:
            lines = _caption_lines(caption, columns).split("\\N")
            if len(lines) > 3:
                raise RenderError(f"镜头 {clip.get('id', '')} 字幕超过三行，请精简字幕或拆分镜头")
            rendered_caption = "\\N".join(lines)
            events.append(f"Dialogue: 0,{_ass_time(clip['_timeline_start'])},{_ass_time(clip['_timeline_end'])},Caption,,0,0,0,,{rendered_caption}")
    return header + "\n".join(events) + "\n"


def render_plan(plan: dict, assets: list[dict], output_path: Path,
                progress: Callable | None = None) -> dict:
    """Render a validated plan to MP4 and return measured output metadata.

    ``progress`` receives a single dict: ``{stage, progress, message}``.
    Video in/out points are source seconds. Image start is zero and end equals
    its display duration. Duration is preserved to the nearest 30 fps frame.
    Existing output files and source media are never overwritten.
    """
    ffmpeg, supports_ass = _ffmpeg_binary()
    ffprobe = shutil.which(os.environ.get("FFPROBE", "ffprobe"))
    if not ffprobe:
        raise RenderError("需要安装 FFmpeg 和 ffprobe")
    output_path = Path(output_path).expanduser().resolve()
    if output_path.exists():
        raise RenderError("输出文件已存在，请使用新的文件名")
    width = _dimension(plan.get("width", 720), "输出宽度")
    height = _dimension(plan.get("height", 1280), "输出高度")
    ratio = plan.get("ratio")
    if ratio:
        try:
            rw, rh = (float(v) for v in str(ratio).split(":"))
            if not math.isfinite(rw + rh) or rw <= 0 or rh <= 0 or abs(width / height - rw / rh) > .01:
                raise ValueError
        except (ValueError, ZeroDivisionError):
            raise RenderError("画幅比例与输出宽高不一致") from None
    source_clips = plan.get("clips")
    if not isinstance(source_clips, list) or not source_clips:
        raise RenderError("剪辑计划至少需要一个镜头")
    asset_map = {}
    for asset in assets:
        if asset.get("id") in asset_map:
            raise RenderError("素材 ID 重复")
        asset_map[asset.get("id")] = asset
    probe_cache: dict[Path, dict] = {}

    def inspect(asset_id: str) -> tuple[dict, Path, dict]:
        asset = asset_map.get(asset_id)
        if not asset or not asset.get("path"):
            raise RenderError(f"找不到素材：{asset_id}")
        path = Path(asset["path"]).expanduser().resolve()
        if not path.is_file():
            raise RenderError(f"素材文件不存在：{path.name}")
        if path == output_path:
            raise RenderError("不能覆盖原始素材")
        if path not in probe_cache:
            probe_cache[path] = _probe(path, ffprobe)
        return asset, path, probe_cache[path]

    clips, cursor, frame_cursor = [], 0.0, 0
    for index, original in enumerate(source_clips):
        clip = dict(original)
        asset, path, info = inspect(clip.get("asset_id"))
        if asset.get("kind") not in {"image", "video"} or not info["video"]:
            raise RenderError(f"第{index + 1}个镜头需要图片或视频素材")
        start = _number(clip.get("start", 0), "素材入点")
        duration = _number(clip.get("duration"), "镜头时长", positive=True)
        speed = _number(clip.get('speed', 1), '播放速度', positive=True)
        original_volume = _number(clip.get('original_volume', 1), '原声音量')
        music_gain = _number(clip.get('music_gain', 1), '本段配乐音量')
        if not .5 <= speed <= 2 or original_volume > 1.5 or music_gain > 1:
            raise RenderError('播放速度或音量超出可执行范围')
        if asset['kind']=='image' and speed != 1:
            raise RenderError('图片不支持播放速度调整')
        end = _number(clip.get("end", start+duration*speed), "素材出点", positive=True)
        if end <= start or abs((end-start)/speed-duration) > .02:
            raise RenderError(f"第{index + 1}个镜头的入出点与时长不一致")
        if asset["kind"] == "video":
            if info["video_duration"] <= 0 or end > info["video_duration"] + .02:
                raise RenderError(f"第{index + 1}个镜头超出真实素材时长")
        elif start != 0:
            raise RenderError("图片素材入点必须为0")
        transition = clip.get("transition", "cut")
        if transition not in {"cut", "fade"}:
            raise RenderError(f"不支持转场：{transition}，可用 cut 或 fade")
        if "transition_duration" in clip and abs(_number(clip["transition_duration"], "转场时长") - .25) > .001:
            raise RenderError("fade 固定为开头0.25秒淡入，不支持自定义转场时长")
        next_frame = round((cursor + duration) * FPS)
        frames = next_frame - frame_cursor
        if frames < 1:
            raise RenderError("镜头时长不能小于一帧")
        clip.update(_path=path, _kind=asset["kind"], _info=info, _source_start=start,
                    _source_duration=end - start, _frames=frames, _duration=frames / FPS,
                    _timeline_start=frame_cursor / FPS, _timeline_end=next_frame / FPS,
                    transition=transition, speed=speed, original_volume=original_volume, music_gain=music_gain)
        clips.append(clip)
        cursor += duration
        frame_cursor = next_frame
    if plan.get("duration") is not None and abs(_number(plan["duration"], "计划时长", positive=True) - cursor) > .05:
        raise RenderError("计划总时长与镜头时长之和不一致")
    total = frame_cursor / FPS
    music = None
    if plan.get("music_asset_id"):
        _, music, music_info = inspect(plan["music_asset_id"])
        if not music_info["audio"] or music_info["duration"] <= 0:
            raise RenderError("配乐素材没有有效音轨")
    music_volume = _number(plan.get("music_volume", .16), "配乐音量")
    music_start = _number(plan.get('music_start', 0), '配乐入点')
    if music and music_start >= music_info['duration']:
        raise RenderError('配乐入点超出素材时长')
    if music_volume > 1:
        raise RenderError("配乐音量应在0到1之间")
    ass = _ass_document(clips, width, height, str(plan.get("font") or "Hiragino Sans GB"))
    has_captions = any(str(clip.get("caption") or "").strip() for clip in clips)
    if has_captions and not supports_ass:
        raise RenderError("当前 FFmpeg 不支持 ASS 字幕，请安装 imageio-ffmpeg 或通过 FFMPEG 指定含 libass 的构建")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def report(stage: str, value: float, message: str) -> None:
        if progress:
            progress({"stage": stage, "progress": round(value, 3), "message": message})

    report("preparing", .02, "素材与时间轴已校验")
    with tempfile.TemporaryDirectory(prefix="render-", dir=output_path.parent) as temporary:
        workdir = Path(temporary)
        (workdir / "captions.ass").write_text(ass, encoding="utf-8")
        names = []
        for index, clip in enumerate(clips):
            duration, frames = clip["_duration"], clip["_frames"]
            # PCM intermediates avoid AAC priming/padding at every join. Encode
            # AAC only once, after the frame-exact picture edit is assembled.
            name = f"clip-{index:04d}.mkv"
            names.append(name)
            args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
            if clip["_kind"] == "video":
                args += ["-ss", str(clip["_source_start"])]
            args += ["-i", str(clip["_path"])]
            audio_input = 0
            if clip["_kind"] == "image" or not clip["_info"]["audio"]:
                args += ["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
                audio_input = 1
            fit = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
            if clip["_kind"] == "image":
                step = .04 / max(frames - 1, 1)
                visual = (f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                          f"crop={width * 2}:{height * 2},setsar=1,"
                          f"zoompan=z='1+min(on,{frames - 1})*{step:.10f}':"
                          f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={width}x{height}:fps={FPS}")
            else:
                visual = (f"trim=duration={clip['_source_duration']:.8f},setpts=(PTS-STARTPTS)/{clip['speed']:.10f},{fit},"
                          f"fps={FPS},tpad=stop_mode=clone:stop_duration={2 / FPS:.8f},trim=end_frame={frames}")
            audio = (f"aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=duration={clip['_source_duration']:.8f},asetpts=PTS-STARTPTS,"
                     f"atempo={clip['speed']:.10f},aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={clip['original_volume']:.8f},apad,atrim=end_sample={frames*(SAMPLE_RATE//FPS)},"
                     f"asetpts=N/SR/TB,afade=t=in:d=0.005,afade=t=out:st={max(0,duration-.005):.8f}:d=0.005")
            if clip["transition"] == "fade":
                fade = min(.25, duration)
                visual += f",fade=t=in:st=0:d={fade:.8f}"
                audio += f",afade=t=in:st=0:d={fade:.8f}"
            filters = f"[0:v:0]{visual},format=yuv420p[v];[{audio_input}:a:0]{audio}[a]"
            args += ["-filter_complex_threads", "1", "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
                     "-t", f"{duration:.8f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "pcm_f32le", "-ar", str(SAMPLE_RATE),
                     "-ac", "2", name]
            _run(args, cwd=workdir)
            report("rendering", .05 + .65 * (index + 1) / len(clips), f"已渲染镜头 {index + 1}/{len(clips)}")
        (workdir / "concat.txt").write_text("".join(f"file '{name}'\nduration {clip['_duration']:.10f}\n" for name,clip in zip(names,clips)), encoding="utf-8")
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1", "-i", "concat.txt"]
        if music:
            args += ["-stream_loop", "-1", "-ss", str(music_start), "-i", str(music)]
            fade_out = max(0, total - min(.5, total / 4))
            envelope = str(clips[-1]['music_gain'])
            for index in reversed(range(len(clips))):
                clip = clips[index]
                start, end, gain = clip['_timeline_start'], clip['_timeline_end'], clip['music_gain']
                previous_gain = clips[index-1]['music_gain'] if index else gain
                ramp = min(.08, clip['_duration']/2)
                local = f"if(lt(t,{start+ramp:.8f}),{previous_gain}+({gain}-{previous_gain})*(t-{start:.8f})/{ramp:.8f},{gain})"
                envelope = f"if(lt(t,{end:.8f}),{local},{envelope})"
            audio = (f"[0:a:0]aresample={SAMPLE_RATE}:async=1:first_pts=0,apad,atrim=duration={total:.8f}[original];"
                     f"[1:a:0]asetpts=PTS-STARTPTS,aresample={SAMPLE_RATE},volume='{music_volume}*({envelope})':eval=frame,atrim=duration={total:.8f},"
                     f"afade=t=in:d={min(.3, total / 4):.8f},afade=t=out:st={fade_out:.8f}:d={total-fade_out:.8f}[music];"
                     "[original][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.7:level=0:latency=1[a]")
        else:
            audio = f"[0:a:0]aresample={SAMPLE_RATE}:async=1:first_pts=0,apad,atrim=duration={total:.8f},alimiter=limit=0.7:level=0:latency=1[a]"
        final_visual = f"setpts=N/({FPS}*TB),"+("ass=captions.ass,format=yuv420p" if has_captions else "format=yuv420p")
        args += ["-filter_complex_threads", "1", "-filter_complex", f"[0:v:0]{final_visual}[v];{audio}",
                 "-map", "[v]", "-map", "[a]", "-t", f"{total:.8f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
                 "-ac", "2", "-movflags", "+faststart", "final.mp4"]
        report("composing", .75, "正在合成字幕和音轨")
        _run(args, cwd=workdir)
        final = workdir / "final.mp4"
        report("verifying", .92, "正在完整解码验证")
        _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror", "-err_detect", "explode", "-i", str(final),
              "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
        measured = _probe(final, ffprobe)
        if (measured["width"], measured["height"]) != (width, height) or not measured["audio"]:
            raise RenderError("输出画幅或音轨验证失败")
        if abs(measured["duration"] - total) > max(.1, 2 / FPS):
            raise RenderError(f"输出时长验证失败：期望{total:.3f}秒，实际{measured['duration']:.3f}秒")
        if abs(measured["video_duration"] - total) > 1 / FPS or int(measured["video"].get("nb_frames", 0)) != frame_cursor:
            raise RenderError("输出视频帧数与计划时间轴不一致")
        if measured["video"].get("codec_name") != "h264" or measured["video"].get("pix_fmt") != "yuv420p" or measured["audio"].get("codec_name") != "aac":
            raise RenderError("输出编码验证失败")
        # Create the destination exclusively. A concurrent render cannot replace it.
        try:
            os.link(final, output_path)
        except FileExistsError as exc:
            raise RenderError("输出文件已存在，请使用新的文件名") from exc
    compiled_timeline = {
        "fps": FPS, "frame_count": frame_cursor, "interval": "[start_frame,end_frame)",
        "clips": [
            {"clip_id": clip.get("id"), "asset_id": clip["asset_id"],
             "start_frame": round(clip["_timeline_start"] * FPS),
             "end_frame": round(clip["_timeline_end"] * FPS),
             "source_start_seconds": clip["_source_start"],
             "source_end_seconds": clip["_source_start"] + clip["_source_duration"],
             "source_time_base": (clip["_info"]["video"] or {}).get("time_base"),
             "kind": clip["_kind"], "transition": clip["transition"],
             "speed": clip['speed'], "original_volume": clip['original_volume'], "music_gain": clip['music_gain'],
             "source_has_audio": bool(clip['_info']['audio']),
             "caption": str(clip.get("caption") or "")}
            for clip in clips
        ],
    }
    def digest(path):
        with Path(path).open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    manifest = {
        "schema_version": 2, "plan_id": plan.get("id"), "plan_version": plan.get("version"),
        "output_sha256": digest(output_path), "compiled_timeline": compiled_timeline,
        "assets": [
            {"asset_id": asset_id, "sha256": digest(asset_map[asset_id]["path"])}
            for asset_id in dict.fromkeys([clip["asset_id"] for clip in clips] + ([plan["music_asset_id"]] if music else []))
        ],
        "renderer_sha256": digest(__file__),
        "ffmpeg_version": _run([ffmpeg, "-version"], timeout=20).stdout.splitlines()[0],
        "width": width, "height": height, "sample_rate": SAMPLE_RATE,
        "font_requested": str(plan.get("font") or "Hiragino Sans GB"),
        "music_asset_id": plan.get("music_asset_id"), "music_volume": music_volume,
        "music_start": music_start, "music_beat_times": plan.get('music_beat_times', []),
        "music_sync_events": plan.get('music_sync_events', []),
        "audio_treatment": {'pitch_preserved': True, 'music_gain_ramp_seconds': .08, 'source_edge_fade_seconds': .005},
        "plan": plan,
    }
    manifest_path = output_path.with_suffix(".manifest.json")
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
    result = {"path": str(output_path), "duration": measured["duration"], "width": width, "height": height,
              "has_audio": True, "compiled_timeline": compiled_timeline, "manifest_path": str(manifest_path)}
    report("completed", 1.0, "成片已通过完整解码验证")
    return result
