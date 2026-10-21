"""Production renderer contract: real frames, offline validation, process cleanup."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import animation_renderer as renderer
from animation_render_worker import compare_frames, visible_text

SCENE = '''<!doctype html><html><head><style>
#stage{position:relative;width:100vw;height:100vh;background:#fff}
#box{position:absolute;left:20px;top:40px;width:80px;height:80px;background:#3245ed}
#overflow{position:absolute;left:1300px;top:20px;white-space:nowrap}
</style></head><body><div id="stage"><div id="box"></div><span id="overflow">溢出检查</span></div>
<script>window.renderAt=async t=>{await Promise.resolve();document.querySelector('#box').style.transform=`translateX(${t*120}px)`;};</script></body></html>'''


class AnimationRendererTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='animation-render-test-')
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rounding_tolerance_requires_identical_scene_state_and_tiny_pixel_changes(self):
        first = Image.new('RGB', (100, 100), (255,255,255))
        repeated = first.copy(); repeated.putpixel((20,20), (254,255,255))
        self.assertTrue(compare_frames(first, repeated, same_state=True)[1]['rounding_tolerated'])
        # Even the same tiny visual difference cannot hide a changed transform.
        self.assertFalse(compare_frames(first, repeated, same_state=False)[1]['rounding_tolerated'])
        repeated.putpixel((20,20), (253,255,255))
        self.assertFalse(compare_frames(first, repeated, same_state=True)[1]['rounding_tolerated'])
        repeated = first.copy()
        for x in range(11):
            repeated.putpixel((x,20), (254,255,255))
        self.assertFalse(compare_frames(first, repeated, same_state=True)[1]['rounding_tolerated'])

    def test_visible_text_requires_visible_ancestry_and_intersection_with_clip_and_viewport(self):
        def ancestor():
            return dict(opacity=1, display='block', visibility='visible', content_visibility='visible',
                        overflow_x='visible', overflow_y='visible', rect=[0,0,1280,720])
        def record(text, rect=None, parents=None):
            return {'text':text,'rects':[rect or [20,20,200,50]],'ancestors':parents or [ancestor()]}
        def changed(**changes):
            value=ancestor(); value.update(changes); return value
        records = [record('现在显示'), record('未入场', parents=[ancestor(), changed(opacity=0)]),
                   record('自身透明', parents=[changed(opacity=0)]),
                   record('祖先隐藏', parents=[ancestor(),changed(display='none')]),
                   record('折叠容器', parents=[ancestor(),changed(content_visibility='hidden')]),
                   record('隐藏文字', parents=[changed(visibility='hidden')]),
                   record('画面之外', rect=[1300,20,1400,50]),
                   record('裁剪之外', parents=[ancestor(),changed(overflow_x='hidden',rect=[300,0,400,720])]),
                   record('部分可见', rect=[-20,20,50,50]),
                   record('子级恢复可见', parents=[ancestor(),changed(visibility='hidden')]),
                   record('现在显示')]
        self.assertEqual(visible_text(records,1280,720), ['现在显示','部分可见','子级恢复可见'])

    def test_real_720p_frames_keyframes_and_overflow_diagnostic(self):
        result = renderer.render_scene(SCENE, self.directory/'scene.mp4', width=1280, height=720, duration=2)
        self.assertEqual((result['frames'], result['fps'], result['duration']), (60, 30, 2))
        self.assertEqual(len(result['checkpoints']), 5)
        self.assertEqual(result['isolation']['network'], 'data_only')
        self.assertFalse(result['isolation']['no_sandbox_flag'])
        self.assertIn('text_overflow', [item['code'] for item in result['diagnostics']])
        first = Image.open(result['checkpoints'][0]['path']).convert('RGB')
        middle = Image.open(result['checkpoints'][2]['path']).convert('RGB')
        self.assertEqual(first.getpixel((30, 50)), (50, 69, 237))
        self.assertEqual(middle.getpixel((30, 50)), (255, 255, 255))
        self.assertEqual(middle.getpixel((150, 50)), (50, 69, 237))
        probe = subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries',
                                'stream=width,height,avg_frame_rate,nb_read_frames','-of','json',result['output_path']],
                               capture_output=True,text=True,check=True)
        stream = json.loads(probe.stdout)['streams'][0]
        self.assertEqual((stream['width'],stream['height'],stream['nb_read_frames'],stream['avg_frame_rate']), (1280,720,'60','30/1'))

    def test_stateful_script_and_external_fetch_are_rejected_without_network_or_output(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path); self.send_response(200); self.end_headers()
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            source = SCENE.replace('window.renderAt=async t=>', "let count=0;fetch('http://127.0.0.1:%d/probe').catch(()=>{});window.renderAt=async t=>" % server.server_port).replace('${t*120}', '${++count*8}')
            with self.assertRaises(renderer.AnimationRenderError) as caught:
                renderer.render_scene(source,self.directory/'bad.mp4',width=160,height=90,duration=2)
            codes = [item['code'] for item in caught.exception.diagnostics]
            self.assertIn('non_deterministic_frame', codes)
            self.assertTrue({'console_error','external_resource_blocked'} & set(codes))
            self.assertEqual(requests, [])
            self.assertFalse((self.directory/'bad.mp4').exists())
            self.assertTrue(caught.exception.checkpoints)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_cancellation_and_timeout_reap_only_owned_children_and_preserve_output(self):
        original_reap = renderer._reap
        owned_runs = []
        def capture_reap(process, owned):
            renderer._remember_children(process.pid,owned)
            owned_runs.append(dict(owned))
            return original_reap(process,owned)
        class Cancelled(Exception):
            pass
        def cancel_on_ready(event):
            if event['stage']=='ready':
                raise Cancelled('stop')
        existing = self.directory/'keep.mp4';existing.write_bytes(b'original')
        with patch.object(renderer,'_reap',capture_reap):
            with self.assertRaises(Cancelled):
                renderer.render_scene(SCENE,existing,width=160,height=90,duration=2,progress=cancel_on_ready)
            self.assertEqual(existing.read_bytes(),b'original')
            with patch.object(renderer,'MAX_RENDER_SECONDS',4), self.assertRaises(renderer.AnimationRenderError) as caught:
                renderer.render_scene('<script>window.renderAt=()=>{while(true){}}</script>',self.directory/'timeout.mp4',width=160,height=90,duration=2)
            self.assertEqual(caught.exception.diagnostics[0]['code'],'render_timeout')
        deadline = time.monotonic()+5
        while True:
            snapshot = renderer._process_snapshot()
            remaining = [pid for owned in owned_runs for pid,started in owned.items() if snapshot.get(pid,(None,None))[1]==started]
            if not remaining or time.monotonic()>deadline:
                break
            time.sleep(.1)
        self.assertFalse(remaining, f'Owned child processes survived: {remaining}')
        self.assertTrue(all(len(owned)>2 for owned in owned_runs))
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':'secret','MINIMAX_API_KEY':'secret','NODE_OPTIONS':'untrusted'}):
            clean = renderer._clean_environment(self.directory)
            self.assertFalse({'DEEPSEEK_API_KEY','MINIMAX_API_KEY','NODE_OPTIONS'} & clean.keys())


if __name__ == '__main__':
    unittest.main()
