"""Read saved render evidence and replay the exact intervals, without model calls."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import media
import storage as db

router = APIRouter()
_cache_lock = threading.Lock()


def _media_path(url):
    if not isinstance(url, str) or not url.startswith('/api/media/'):
        raise HTTPException(409, '此版本缺少媒体记录')
    try:
        return db.safe_media_path(url.removeprefix('/api/media/'))
    except FileNotFoundError:
        raise HTTPException(404, '原始文件已不在工作台') from None


def _evidence(render_id):
    render = db.get('renders', render_id)
    if not render:
        raise HTTPException(404, '视频版本不存在')
    try:
        manifest = json.loads(_media_path(render.get('manifest_url')).read_text())
    except (ValueError, OSError):
        raise HTTPException(409, '此版本的剪辑记录不可读') from None
    if manifest.get('schema_version') not in {1, 2} or manifest.get('plan_id') != render.get('plan_id') or manifest.get('output_sha256') != render.get('sha256'):
        raise HTTPException(409, '剪辑记录与视频版本不一致')
    timeline = manifest.get('compiled_timeline') or {}
    fps = timeline.get('fps', 0)
    if not isinstance(fps, (float, int)) or not math.isfinite(fps) or fps <= 0 or not timeline.get('clips'):
        raise HTTPException(409, '此版本没有可回放的剪辑记录')
    if render.get('compiled_timeline') != timeline:
        raise HTTPException(409, '实际时间线记录不一致')
    return render, manifest


def _span(value):
    return isinstance(value, (float, int)) and math.isfinite(value)


def _unique_duration(intervals):
    total, right = 0, -1
    for start, end in sorted(intervals):
        total += max(0, end - max(right, start))
        right = max(right, end)
    return total


def breakdown(render_id):
    render, manifest = _evidence(render_id)
    timeline, plan = manifest['compiled_timeline'], manifest.get('plan') or {}
    fps = timeline['fps']
    planned = {clip['id']: clip for clip in plan.get('clips', [])}
    assets, nodes, intervals = {}, [], {}
    previous, reordered, cursor = {}, False, 0
    for index, clip in enumerate(timeline['clips']):
        start, end = clip.get('source_start_seconds'), clip.get('source_end_seconds')
        first, last = clip.get('start_frame'), clip.get('end_frame')
        if not all(_span(v) for v in [start, end, first, last]) or start < 0 or end <= start or first != cursor or last <= first:
            raise HTTPException(409, '此版本的片段时间记录不完整')
        cursor = last
        asset_id, clip_id = clip['asset_id'], clip['clip_id']
        asset = assets.setdefault(asset_id, db.get('assets', asset_id) or {})
        original = planned.get(clip_id) or {}
        if not original or original.get('asset_id') != asset_id:
            raise HTTPException(409, '片段与方案无法对应')
        if asset_id in previous and start < previous[asset_id]:
            reordered = True
        previous[asset_id] = start
        if clip.get('kind') == 'video':
            intervals.setdefault(asset_id, []).append((start, end))
        changed = [key for key, actual in [('start', start), ('end', end), ('caption', clip.get('caption', '')), ('transition', clip.get('transition'))]
                   if (abs(original.get(key, -1)-actual) > 1e-6 if key in {'start','end'} and _span(original.get(key)) else original.get(key) != actual)]
        base = f'/api/renders/{render_id}/clips/{clip_id}'
        speed, original_volume, music_gain = (clip.get(k, 1) for k in ('speed', 'original_volume', 'music_gain'))
        if not all(_span(v) for v in (speed, original_volume, music_gain)) or not .5 <= speed <= 2 or not 0 <= original_volume <= 1.5 or not 0 <= music_gain <= 1:
            raise HTTPException(409, '片段处理记录无效')
        beat = None
        if original.get('beat_sync') and original.get('beat_alignment') and manifest.get('music_asset_id'):
            music_time = last/fps+float(manifest.get('music_start', 0))
            observed = manifest.get('music_sync_events') or [{'time': t, 'kind':'pulse'} for t in manifest.get('music_beat_times', [])]
            if observed:
                nearest = min(observed, key=lambda e: abs(e['time']-music_time))
                beat = {'music_time': nearest['time'], 'kind':nearest['kind'],
                        'error_ms': round((music_time-nearest['time'])*1000, 1),
                        'matched': abs(music_time-nearest['time']) <= .05}
        nodes.append({'id': clip_id, 'order': index+1, 'asset_id': asset_id,
            'asset_name': asset.get('name', '原素材缺失'), 'kind': clip.get('kind'),
            'source_start': start, 'source_end': end, 'output_start': first/fps, 'output_end': last/fps,
            'duration': (last-first)/fps, 'caption': clip.get('caption', ''), 'transition': clip.get('transition'),
            'source_has_audio': bool(asset.get('has_audio')), 'frame_count': last-first,
            'speed': speed, 'original_volume': original_volume, 'music_gain': music_gain, 'beat_alignment': beat,
            'decision': {key: original.get(key) for key in ['label','role','caption','transition','start','end','duration',
                'cut_reason','speed_reason','sound_reason','action_evidence']},
            'changed_fields': changed, 'thumbnail_url': base+'/thumbnail',
            'source_url': base+'/source', 'result_url': base+'/result'})
    if cursor != timeline.get('frame_count'):
        raise HTTPException(409, '片段未覆盖完整视频')
    return {'render_id': render_id, 'plan_id': render['plan_id'], 'actor': plan.get('origin', {}).get('actor'),
        'model': plan.get('model'), 'summary': plan.get('summary'),
        'strategy': (plan.get('candidate_strategy') or {}).get('label'),
        'input_kind': 'analysis' if plan.get('request_context', {}).get('image_count') == 0 else 'unrecorded',
        'nodes': nodes, 'stats': {'clips': len(nodes), 'sources': len(assets),
            'captions': sum(bool(n['caption'].strip()) for n in nodes),
            'fades': sum(n['transition']=='fade' for n in nodes),
            'cut_joins': sum(n['transition']=='cut' for n in nodes[1:]),
            'speed_changes': sum(abs(n['speed']-1)>.001 for n in nodes),
            'beat_joins': sum(bool(n['beat_alignment'] and n['beat_alignment']['matched']) for n in nodes[:-1]),
            'music_ducks': sum(n['music_gain']<1 for n in nodes) if manifest.get('music_asset_id') else 0,
            'reordered': reordered, 'duration': cursor/fps,
            'source_duration': round(sum(float(assets[k].get('duration') or 0) for k in intervals), 3),
            'unique_source_duration': round(sum(_unique_duration(rows) for rows in intervals.values()), 3)},
        'execution': {'fps': fps, 'width': manifest.get('width'), 'height': manifest.get('height'),
            'schema_version': manifest.get('schema_version'),
            'music_volume': manifest.get('music_volume'), 'music_start': manifest.get('music_start', 0),
            'music_added': bool(manifest.get('music_asset_id')), 'font': manifest.get('font_requested'),
            'image_motion': '4% slow zoom', 'fade_seconds': .25,
            'renderer_matches_current': manifest.get('renderer_sha256') == media.file_hash(Path(__file__).with_name('renderer.py'))}}


def excerpt(render_id, clip_id, mode):
    """Cache review proxies under previews; never create or alter an asset/render."""
    render, manifest = _evidence(render_id)
    data = breakdown(render_id)
    node = next((n for n in data['nodes'] if n['id']==clip_id), None)
    if not node:
        raise HTTPException(404, '片段不存在')
    asset = db.get('assets', node['asset_id']) or {}
    if mode == 'result':
        path = _media_path(render['url'])
        expected = manifest['output_sha256']
        start, end, kind = node['output_start'], node['output_end'], 'video'
    else:
        path = _media_path(asset.get('url'))
        expected = next((a['sha256'] for a in manifest['assets'] if a['asset_id']==node['asset_id']), None)
        start, end, kind = node['source_start'], node['source_end'], node['kind']
    if not expected or media.file_hash(path) != expected:
        raise HTTPException(409, '文件与生成时的指纹不一致，不能用来核对这条成片')
    if kind == 'image' and mode == 'source':
        return path
    fingerprint = hashlib.sha256(json.dumps([2,expected,start,end,mode,kind]).encode()).hexdigest()
    directory = db.DATA/'previews'/'cut-inspector'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/(fingerprint+('.jpg' if mode=='thumbnail' else '.mp4'))
    with _cache_lock:
        if target.is_file():
            return target
        temp = directory/(fingerprint+'.'+uuid.uuid4().hex+target.suffix)
        try:
            if mode == 'thumbnail':
                media.thumbnail(path, kind, temp, at=start)
            else:
                media.run([media.FFMPEG,'-nostdin','-hide_banner','-loglevel','error','-y',
                    '-ss',str(start),'-i',path,'-t',str(end-start),'-map','0:v:0','-map','0:a:0?',
                    '-vf','scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1',
                    '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-threads','1',
                    '-c:a','aac','-movflags','+faststart',temp],timeout=120)
                if not temp.is_file() or not temp.stat().st_size:
                    raise ValueError('片段回放文件未生成')
            temp.replace(target)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        finally:
            temp.unlink(missing_ok=True)
    return target


@router.get('/api/renders/{render_id}/breakdown')
def get_breakdown(render_id: str):
    return breakdown(render_id)


@router.get('/api/renders/{render_id}/clips/{clip_id}/{mode}')
def get_excerpt(render_id: str, clip_id: str, mode: str):
    if mode not in {'source','result','thumbnail'}:
        raise HTTPException(404, '回放类型不存在')
    return FileResponse(excerpt(render_id, clip_id, mode), headers={'Cache-Control':'private, max-age=3600'})
