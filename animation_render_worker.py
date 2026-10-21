"""Private subprocess for deterministic, offline HTML frames; never serve this HTML."""
from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright

SCENE_URL = 'https://animation.invalid/scene'
CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; media-src data:; connect-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; sandbox allow-scripts"

INSPECT_LAYOUT = """() => {
  const problems = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (problems.length >= 30) break;
    if (!Array.from(el.childNodes).some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim())) continue;
    const css = getComputedStyle(el), r = el.getBoundingClientRect();
    if (css.display === 'none' || css.visibility === 'hidden' || +css.opacity === 0 || !r.width || !r.height) continue;
    const range = document.createRange(); range.selectNodeContents(el);
    const textRects = Array.from(range.getClientRects());
    const outside = textRects.some(t => t.left < -1 || t.top < -1 || t.right > innerWidth + 1 || t.bottom > innerHeight + 1);
    const clipped = (['hidden','clip'].includes(css.overflowX) && el.scrollWidth > el.clientWidth + 2) || (['hidden','clip'].includes(css.overflowY) && el.scrollHeight > el.clientHeight + 2);
    if (outside || clipped) problems.push({tag:el.tagName.toLowerCase(), id:el.id, text:el.textContent.trim().slice(0,120), outside, clipped});
  }
  return {text_overflow:problems, running_animations:document.getAnimations().filter(a => a.playState === 'running').length};
}"""

# A one-level antialiasing difference is only tolerated when the actual scene
# state is identical. In particular, a tiny CSS/DOM displacement is not waived.
FRAME_STATE = """() => JSON.stringify({
  markup: document.documentElement.outerHTML,
  scroll: [scrollX, scrollY],
  elements: Array.from(document.querySelectorAll('*')).map(el => {
    const style = pseudo => { const c = getComputedStyle(el, pseudo); return Array.from(c).map(k => [k,c.getPropertyValue(k)]); };
    const r = el.getBoundingClientRect();
    return {rect:[r.x,r.y,r.width,r.height], style:style(null),
      before:style('::before'), after:style('::after'),
      canvas:el.tagName === 'CANVAS' ? el.toDataURL() : null};
  })
})"""

TEXT_RECORDS = """() => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const records = [];
  const rect = r => [r.left, r.top, r.right, r.bottom];
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.replace(/\\s+/g, ' ').trim();
    if (!text || !node.parentElement) continue;
    const range = document.createRange(); range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects(), rect);
    if (!rects.length) continue;
    const ancestors = [];
    for (let el = node.parentElement; el; el = el.parentElement) {
      const s = getComputedStyle(el);
      ancestors.push({opacity:Number(s.opacity), display:s.display, visibility:s.visibility,
        content_visibility:s.contentVisibility, overflow_x:s.overflowX, overflow_y:s.overflowY,
        rect:rect(el.getBoundingClientRect())});
    }
    records.push({text, rects, ancestors});
  }
  return records;
}"""


def visible_text(records, width, height):
    """DOM text in the frame, excluding hidden ancestry and clipped rectangles.

    This is visibility evidence, not OCR: canvas text and occlusion by another
    element still require the checkpoint image. CSS visibility can be explicitly
    restored by a child, unlike display:none or a zero-opacity ancestor.
    """
    visible = []
    for record in records:
        ancestors = record['ancestors']
        if not ancestors or ancestors[0]['visibility'] in ('hidden', 'collapse'):
            continue
        clip = [0, 0, width, height]
        opacity = 1
        for ancestor in ancestors:
            opacity *= ancestor['opacity']
            if ancestor['display'] == 'none' or ancestor['content_visibility'] == 'hidden':
                opacity = 0
                break
            bounds = ancestor['rect']
            if ancestor['overflow_x'] in ('hidden', 'clip', 'scroll', 'auto'):
                clip[0], clip[2] = max(clip[0], bounds[0]), min(clip[2], bounds[2])
            if ancestor['overflow_y'] in ('hidden', 'clip', 'scroll', 'auto'):
                clip[1], clip[3] = max(clip[1], bounds[1]), min(clip[3], bounds[3])
        if opacity <= 0:
            continue
        on_screen = any(min(rect[2], clip[2]) > max(rect[0], clip[0])
                        and min(rect[3], clip[3]) > max(rect[1], clip[1])
                        for rect in record['rects'])
        text = record['text']
        if on_screen and text not in visible:
            visible.append(text)
    return visible


def compare_frames(image, duplicate, *, same_state):
    difference = ImageChops.difference(image, duplicate)
    channels = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    histogram = maximum.histogram()
    changed = sum(histogram[1:])
    channel_difference = max(i for i, count in enumerate(histogram) if count)
    ratio = changed / (image.width * image.height)
    tolerated = same_state and channel_difference <= 1 and ratio <= .001
    return difference, {'changed_pixels': changed, 'changed_pixel_ratio': round(ratio, 9),
        'max_channel_difference': channel_difference,
        'mean_absolute_difference': round(sum(ImageStat.Stat(difference).mean)/3, 6),
        'difference_bounds': maximum.getbbox(), 'scene_state_equal': same_state,
        'rounding_tolerated': tolerated,
        'tolerance': {'max_channel_difference': 1, 'max_changed_pixel_ratio': .001}}


def emit(event, value):
    print(json.dumps({'event': event, event: value}, ensure_ascii=False), flush=True)


def main(config):
    started = time.monotonic()
    diagnostics, saved = [], []
    evidence = Path(config['evidence_dir'])
    encoder = None
    browser = None
    context = None
    encoder_error = evidence / 'encoder.stderr'
    error_stream = encoder_error.open('wb')
    result = {'ok': False, 'diagnostics': diagnostics, 'checkpoints': saved}
    seen = set()

    def diagnostic(code, message, *, severity='error', **detail):
        identity = (code, message, detail.get('frame'))
        if identity not in seen and len(diagnostics) < 100:
            seen.add(identity)
            diagnostics.append({'code': code, 'severity': severity, 'message': str(message)[:1200], **detail})

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    executable_path=config['chrome'], headless=True, chromium_sandbox=True,
                    timeout=20000, args=['--enable-automation', '--disable-background-networking', '--disable-component-update',
                        '--disable-domain-reliability', '--disable-sync', '--no-first-run',
                        '--disable-features=MediaRouter', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'],
                )
                command = browser.new_browser_cdp_session().send('Browser.getBrowserCommandLine')['arguments']
                if any(arg == '--no-sandbox' for arg in command):
                    raise ValueError('浏览器 sandbox 未启用，已停止渲染')
                result['isolation'] = {'sandbox_requested': True, 'no_sandbox_flag': False,
                    'network': 'data_only', 'profile': 'ephemeral', 'clock': 'paused', 'browser_version': browser.version}
                context = browser.new_context(viewport={'width': config['width'], 'height': config['height']},
                    device_scale_factor=1, locale='zh-CN', timezone_id='UTC', color_scheme='light',
                    service_workers='block', accept_downloads=False, permissions=[])
                supplied = False

                def route(request_route):
                    nonlocal supplied
                    request = request_route.request
                    if not supplied and request.url == SCENE_URL and request.is_navigation_request():
                        supplied = True
                        request_route.fulfill(status=200, content_type='text/html; charset=utf-8',
                            headers={'Content-Security-Policy': CSP, 'Cache-Control': 'no-store'},
                            body=Path(config['source_path']).read_text(encoding='utf-8'))
                    elif request.url.startswith('data:'):
                        request_route.continue_()
                    else:
                        # Do not leak arbitrary URL query values into diagnostics.
                        diagnostic('external_resource_blocked', '场景尝试使用非 data 外部资源')
                        request_route.abort('blockedbyclient')

                context.route('**/*', route)
                context.route_web_socket('**/*', lambda socket: socket.close(code=1008, reason='offline renderer'))
                context.add_init_script("""(() => {
                  window.open = () => null;
                  for (const name of ['RTCPeerConnection','webkitRTCPeerConnection']) {
                    if (name in window) Object.defineProperty(window,name,{value:undefined,writable:false,configurable:false});
                  }
                })()""")
                page = context.new_page()
                page.set_default_timeout(12000)
                page.on('console', lambda message: diagnostic('console_error', message.text) if message.type == 'error' else None)
                page.on('pageerror', lambda error: diagnostic('javascript_error', str(error)))
                page.on('dialog', lambda dialog: dialog.dismiss())
                context.on('page', lambda popup: popup.close() if popup != page else None)
                page.clock.install(time=0)
                page.clock.pause_at(1000000)
                page.goto(SCENE_URL, wait_until='load', timeout=15000)
                page.add_style_tag(content='html,body{margin:0;width:100%;height:100%;overflow:hidden;font-family:"PingFang SC",sans-serif}*{transition:none!important;caret-color:transparent!important}')
                page.evaluate('async () => { await document.fonts.ready; await Promise.all(Array.from(document.images).map(i => i.decode().catch(() => {}))); }')
                if not page.evaluate("typeof window.renderAt === 'function'"):
                    diagnostic('missing_render_at', '场景必须定义 window.renderAt(t)')
                    raise ValueError('场景缺少 renderAt(t)')
                emit('progress', {'stage': 'ready', 'completed': 0, 'total': config['frames'], 'progress': 0})

                def frame_at(index):
                    at = index / config['fps']
                    page.evaluate('async t => { await window.renderAt(t); }', at)
                    return page.screenshot(type='png', animations='allow', caret='hide', timeout=12000)

                checks = {}
                for index in config['diagnostic_indices']:
                    first = frame_at(index)
                    first_state = page.evaluate(FRAME_STATE)
                    first_text = visible_text(page.evaluate(TEXT_RECORDS), config['width'], config['height']) if index in config['checkpoint_indices'] else None
                    # An intervening different t detects stateful accumulation, not just random flicker.
                    frame_at(0 if index else config['frames'] - 1)
                    repeated = frame_at(index)
                    repeated_state = page.evaluate(FRAME_STATE)
                    image = Image.open(io.BytesIO(first)).convert('RGB')
                    duplicate = Image.open(io.BytesIO(repeated)).convert('RGB')
                    if image.tobytes() != duplicate.tobytes():
                        difference, metrics = compare_frames(image, duplicate, same_state=first_state == repeated_state)
                        paths = {}
                        for suffix, value in [('first', image), ('repeated', duplicate), ('difference', difference)]:
                            path = evidence / f'determinism-{index:06d}-{suffix}.png'
                            value.save(path)
                            paths[suffix] = str(path)
                        tolerated = metrics['rounding_tolerated']
                        diagnostic('raster_rounding' if tolerated else 'non_deterministic_frame',
                            '场景状态完全一致，少量像素出现 1 级栅格化舍入差异' if tolerated else '相同 t 的重复采样画面不同；renderAt 必须由 t 完整决定画面',
                            severity='warning' if tolerated else 'error', frame=index, time=index/config['fps'],
                            **metrics, evidence=paths,
                            first_state_sha256=hashlib.sha256(first_state.encode()).hexdigest(),
                            repeated_state_sha256=hashlib.sha256(repeated_state.encode()).hexdigest())
                    stat = ImageStat.Stat(image.resize((96, 96)))
                    spread = max(stat.stddev)
                    checks[index] = {'frame': index, 'time': index/config['fps'], 'pixel_stddev': round(spread, 3)}
                    layout = page.evaluate(INSPECT_LAYOUT)
                    if layout['text_overflow']:
                        diagnostic('text_overflow', '检测到文字超出画面或容器，需视觉核对', severity='warning', frame=index, time=index/config['fps'], elements=layout['text_overflow'])
                    if layout['running_animations']:
                        diagnostic('uncontrolled_animation', '存在实时播放的动画；renderAt 中须暂停并设置动画 currentTime', frame=index, time=index/config['fps'])
                    if index in config['checkpoint_indices']:
                        path = evidence / f'frame-{index:06d}.png'
                        path.write_bytes(first)
                        saved.append({'time': index/config['fps'], 'frame': index, 'path': str(path), 'visible_text': first_text})
                if checks and all(item['pixel_stddev'] < .5 for item in checks.values()):
                    diagnostic('blank_scene', '所有采样帧几乎为单色，未检测到可辨识内容')
                elif any(item['pixel_stddev'] < .5 for item in checks.values()):
                    diagnostic('blank_keyframe', '部分采样帧几乎为单色，可能是入场或留白', severity='warning')
                result['frame_checks'] = list(checks.values())
                if any(item['severity'] == 'error' for item in diagnostics):
                    raise ValueError('场景校验未通过')

                encoder = subprocess.Popen([config['ffmpeg'], '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'image2pipe', '-vcodec', 'png', '-framerate', str(config['fps']), '-i', 'pipe:0',
                    '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
                    '-threads', '2', '-movflags', '+faststart', config['output_path']],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=error_stream)
                for index in range(config['frames']):
                    encoder.stdin.write(frame_at(index))
                    if index % 10 == 0 or index == config['frames'] - 1:
                        emit('progress', {'stage': 'frames', 'completed': index + 1, 'total': config['frames'], 'progress': (index + 1) / config['frames']})
                encoder.stdin.close()
                if encoder.wait(timeout=30):
                    diagnostic('encoding_failed', 'FFmpeg 编码失败')
                    raise ValueError('视频编码失败')
                if any(item['severity'] == 'error' for item in diagnostics):
                    raise ValueError('渲染过程中出现脚本或资源错误')
                result.update(ok=True, duration=config['frames']/config['fps'], frames=config['frames'], fps=config['fps'],
                    width=config['width'], height=config['height'], render_seconds=round(time.monotonic()-started, 3))
            finally:
                if context:
                    context.close()
                if browser:
                    browser.close()
    except Exception as exc:
        result['message'] = str(exc)[:1200]
        if not any(item['severity'] == 'error' for item in diagnostics):
            diagnostic('render_failed', str(exc))
    finally:
        if encoder and encoder.poll() is None:
            encoder.terminate()
            try:
                encoder.wait(timeout=3)
            except subprocess.TimeoutExpired:
                encoder.kill(); encoder.wait(timeout=3)
        error_stream.close()
        if encoder_error.exists() and not encoder_error.stat().st_size:
            encoder_error.unlink()
        result['render_seconds'] = round(time.monotonic()-started, 3)
        (evidence / 'render-diagnostics.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        emit('result', result)


if __name__ == '__main__':
    main(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
