"""Isolated, offline HTML animation production renderer (no user browser/profile)."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

MAX_RENDER_SECONDS = 720
CHROME = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


class AnimationRenderError(RuntimeError):
    def __init__(self, message, *, diagnostics=None, checkpoints=None):
        super().__init__(message)
        self.diagnostics = diagnostics or []
        self.checkpoints = checkpoints or []


def _clean_environment(directory):
    return {
        'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
        'HOME': str(directory), 'TMPDIR': str(directory),
        'LANG': 'en_US.UTF-8', 'LC_ALL': 'C',
        'PYTHONNOUSERSITE': '1', 'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1',
    }


def _process_snapshot():
    """Start times prevent signalling a PID that has since been reused."""
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,lstart='], capture_output=True, text=True,
                            timeout=3, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    rows = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            try:
                rows[int(parts[0])] = (int(parts[1]), parts[2])
            except ValueError:
                pass
    return rows


def _remember_children(pid, owned):
    snapshot = _process_snapshot()
    parents = {pid} | {child for child, started in owned.items() if snapshot.get(child, (None, None))[1] == started}
    while True:
        children = {child for child, (parent, _) in snapshot.items() if parent in parents}
        if children <= parents:
            break
        parents |= children
    for child in parents:
        if child in snapshot:
            owned[child] = snapshot[child][1]
    return snapshot


def _reap(process, owned):
    # Chromium can own a separate process group; terminate owned descendants too.
    # Never use pkill/name matching, which could touch the user's normal Chrome.
    try:
        _remember_children(process.pid, owned)
    except (OSError, subprocess.SubprocessError):
        pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            snapshot = _process_snapshot()
        except (OSError, subprocess.SubprocessError):
            snapshot = {}
        for pid, started in list(owned.items()):
            if snapshot.get(pid, (None, None))[1] != started:
                continue
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            pass
        if sig == signal.SIGTERM:
            time.sleep(.15)
    process.wait(timeout=3)


def render_scene(source_html, output_path, *, width, height, duration, fps=30, progress=None, checkpoints=None):
    """Render frame i at exactly i/fps, returning MP4 and retained checkpoint PNGs.

    `progress(event)` receives stage/completed/total/progress. Raising from the
    callback cancels the operation; its exception is preserved after cleanup.
    `checkpoints` is an optional sequence of seconds, rounded to actual frames.
    The caller owns project-level duration limits (the renderer allows 2–60 s).
    """
    if not isinstance(source_html, str) or not source_html.strip() or len(source_html.encode('utf-8')) > 5_000_000:
        raise AnimationRenderError('场景 HTML 为空或超过 5 MB')
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (width, height, fps)):
        raise AnimationRenderError('画面尺寸和帧率必须为整数')
    if min(width, height) < 64 or max(width, height) > 1280 or width * height > 1280 * 720 or width % 2 or height % 2:
        raise AnimationRenderError('画面须使用偶数尺寸，最高 720P')
    if not 1 <= fps <= 60 or isinstance(duration, bool):
        raise AnimationRenderError('帧率或时长无效')
    try:
        duration = float(duration)
    except (TypeError, ValueError) as exc:
        raise AnimationRenderError('时长必须为数字') from exc
    if not math.isfinite(duration) or not 2 <= duration <= 60:
        raise AnimationRenderError('场景时长须为 2–60 秒')
    frames = round(duration * fps)
    checkpoint_times = list(checkpoints) if checkpoints is not None else [0, duration * .25, duration * .5, duration * .75, (frames - 1) / fps]
    if len(checkpoint_times) > 12:
        raise AnimationRenderError('关键帧最多 12 张')
    indices = set()
    for value in checkpoint_times:
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= duration:
            raise AnimationRenderError('关键帧时间须在场景范围内')
        indices.add(min(frames - 1, round(value * fps)))
    # Always diagnose at least three separated points, even if no PNGs were requested.
    diagnostic_indices = sorted(indices | {0, frames // 2, frames - 1})
    chrome = str(CHROME) if CHROME.is_file() else shutil.which('chromium') or shutil.which('google-chrome')
    ffmpeg = shutil.which('ffmpeg')
    if not chrome or not ffmpeg:
        raise AnimationRenderError('缺少 Chromium 或 FFmpeg')
    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() != '.mp4':
        raise AnimationRenderError('输出格式须为 MP4')
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence_dir = output.parent / f'{output.stem}-keyframes-{uuid.uuid4().hex[:10]}'
    evidence_dir.mkdir(mode=0o700)
    started = time.monotonic()
    deadline = started + min(MAX_RENDER_SECONDS, 60 + duration * 10)
    result = None
    with tempfile.TemporaryDirectory(prefix='animation-render-') as temporary:
        private = Path(temporary)
        source = private / 'scene.html'
        source.write_text(source_html, encoding='utf-8')
        config = {'source_path': str(source), 'output_path': str(private / 'result.mp4'),
                  'evidence_dir': str(evidence_dir), 'width': width, 'height': height,
                  'duration': duration, 'fps': fps, 'frames': frames,
                  'checkpoint_indices': sorted(indices), 'diagnostic_indices': diagnostic_indices,
                  'chrome': chrome, 'ffmpeg': ffmpeg}
        config_path = private / 'request.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')
        worker = Path(__file__).with_name('animation_render_worker.py')
        owned = {}
        with (private / 'worker.stderr').open('wb') as stderr:
            process = subprocess.Popen([sys.executable, '-u', str(worker), str(config_path)],
                                       cwd=private, env=_clean_environment(private), start_new_session=True,
                                       stdout=subprocess.PIPE, stderr=stderr)
            buffer = b''
            try:
                while True:
                    _remember_children(process.pid, owned)
                    if time.monotonic() > deadline:
                        raise AnimationRenderError('场景渲染超过总时限', diagnostics=[{'code': 'render_timeout', 'severity': 'error', 'message': '脚本可能未结束或 renderAt 未完成'}])
                    ready, _, _ = select.select([process.stdout], [], [], .2)
                    if ready:
                        chunk = os.read(process.stdout.fileno(), 65536)
                        buffer += chunk
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            if not line:
                                continue
                            event = json.loads(line)
                            if event.get('event') == 'result':
                                result = event['result']
                            elif event.get('event') == 'progress' and progress:
                                progress(event['progress'])
                        if not chunk and process.poll() is not None:
                            break
                    elif process.poll() is not None:
                        break
                process.wait(timeout=3)
                if not result:
                    raise AnimationRenderError('渲染进程未返回结果', diagnostics=[{'code': 'worker_failed', 'severity': 'error', 'message': '渲染进程意外退出'}])
                if not result.get('ok'):
                    raise AnimationRenderError(result.get('message', '场景渲染失败'), diagnostics=result.get('diagnostics'), checkpoints=result.get('checkpoints'))
                if process.returncode or not Path(config['output_path']).is_file():
                    raise AnimationRenderError('渲染输出不完整', diagnostics=result.get('diagnostics'), checkpoints=result.get('checkpoints'))
                os.replace(config['output_path'], output)
            except AnimationRenderError as exc:
                if not exc.checkpoints:
                    exc.checkpoints = [{'frame': int(path.stem.split('-')[-1]),
                                        'time': int(path.stem.split('-')[-1])/fps, 'path': str(path)}
                                       for path in sorted(evidence_dir.glob('frame-*.png'))]
                (evidence_dir / 'render-failure.json').write_text(json.dumps({
                    'message': str(exc), 'diagnostics': exc.diagnostics, 'checkpoints': exc.checkpoints,
                    'render_seconds': round(time.monotonic()-started, 3)}, ensure_ascii=False, indent=2), encoding='utf-8')
                raise
            finally:
                _reap(process, owned)
                process.stdout.close()
    result.update(output_path=str(output), requested_duration=duration, render_seconds=round(time.monotonic() - started, 3))
    result.pop('ok', None)
    (evidence_dir / 'render-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
