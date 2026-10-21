"""Saved evidence, exact source interval, immutable-data and cache checks; no model."""
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import storage as db
import media
import render_inspector as inspector

class RenderInspectorTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        scope=patch.object(db,'DATA',Path(self.temp.name));scope.start();self.addCleanup(scope.stop);db.initialize()
        self.source=db.DATA/'assets/source.mp4';self.output=db.DATA/'renders/output.mp4'
        media.run([media.FFMPEG,'-v','error','-f','lavfi','-i','color=red:s=160x90:d=1:r=30','-f','lavfi','-i','color=blue:s=160x90:d=1:r=30','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-threads','1',self.source])
        media.run([media.FFMPEG,'-v','error','-ss','1.25','-i',self.source,'-t','0.5','-c:v','libx264','-threads','1',self.output])
        self.plan={'id':'p1','origin':{'actor':'model'},'model':'deepseek-flash','clips':[{'id':'c1','asset_id':'a1','start':1.25,'end':1.75,'duration':.5,'caption':'','transition':'cut','role':'蓝色收束','label':'蓝色画面'}]}
        self.timeline={'fps':30,'frame_count':15,'clips':[{'clip_id':'c1','asset_id':'a1','source_start_seconds':1.25,'source_end_seconds':1.75,'start_frame':0,'end_frame':15,'kind':'video','caption':'','transition':'cut'}]}
        db.create('assets',{'id':'a1','url':'/api/media/assets/source.mp4','path':str(self.source),'kind':'video','duration':2,'name':'source.mp4'})
        self.manifest={'schema_version':1,'plan_id':'p1','output_sha256':media.file_hash(self.output),'compiled_timeline':self.timeline,'plan':self.plan,'assets':[{'asset_id':'a1','sha256':media.file_hash(self.source)}],'width':160,'height':90}
        (db.DATA/'renders/output.manifest.json').write_text(json.dumps(self.manifest))
        db.create('renders',{'id':'r1','plan_id':'p1','url':'/api/media/renders/output.mp4','manifest_url':'/api/media/renders/output.manifest.json','sha256':self.manifest['output_sha256'],'compiled_timeline':self.timeline})
        app=FastAPI();app.include_router(inspector.router);self.client=TestClient(app)
    def test_frozen_decisions_and_executed_interval(self):
        db.create('plans',{**self.plan,'clips':[]})
        result=self.client.get('/api/renders/r1/breakdown');self.assertEqual(result.status_code,200)
        data=result.json();self.assertEqual(data['nodes'][0]['decision']['role'],'蓝色收束')
        self.assertEqual(data['nodes'][0]['output_end'],.5);self.assertEqual(data['stats']['unique_source_duration'],.5)
        self.assertEqual(data['stats']['fades'],0)
    def test_precise_excerpt_cache_and_no_asset_creation(self):
        path=inspector.excerpt('r1','c1','source');stamp=path.stat().st_mtime_ns
        self.assertAlmostEqual(media.probe(path)['duration'],.5,delta=.04)
        preview=inspector.excerpt('r1','c1','thumbnail')
        with Image.open(preview) as image:
            red,green,blue=image.getpixel((40,40));self.assertGreater(blue,180);self.assertLess(red,30)
        with patch.object(media,'run',side_effect=AssertionError('cache should not transcode')):
            self.assertEqual(inspector.excerpt('r1','c1','source').stat().st_mtime_ns,stamp)
        self.assertEqual(len(db.records('assets')),1);self.assertEqual(len(db.records('renders')),1)
        self.assertEqual(self.client.get('/api/renders/r1/clips/c1/result').status_code,200)
    def test_wrong_version_unknown_clip_and_changed_source_rejected(self):
        self.assertEqual(self.client.get('/api/renders/r1/clips/missing/source').status_code,404)
        self.source.write_bytes(self.source.read_bytes()+b'changed')
        self.assertEqual(self.client.get('/api/renders/r1/clips/c1/source').status_code,409)
        db.update('renders','r1',{'plan_id':'other'})
        self.assertEqual(self.client.get('/api/renders/r1/breakdown').status_code,409)

if __name__=='__main__':unittest.main()
