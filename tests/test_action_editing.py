"""Action spans stay fixed when a requested music join is compiled."""
import copy, unittest, tempfile
from pathlib import Path
from unittest.mock import patch
import storage as db
from action_editing import align_beats, visible_actions, planning_evidence
from workflow import validate_plan

class ActionEditTest(unittest.TestCase):
    def test_planning_measurements_keep_times_and_energy_without_mutating_compiler_input(self):
        evidence={'visual':{},'audio':{'m':{'summary':{},'sound_source_type':'unclassified',
            'beat_candidates':[{'time':4.1,'grid_time':4.09}],
            'onsets':[{'time':4.1,'strength':.8,'energy_dbfs':-12,'spectral_flux':.42}],
            'energy_by_second':[{'start':4,'end':5,'mean_dbfs':-20,'peak_window_dbfs':-10}]}}}
        original=copy.deepcopy(evidence)
        compact=planning_evidence(evidence)['audio']['m']
        self.assertEqual(evidence,original)
        self.assertEqual(compact['beat_candidate_times'],[4.1])
        self.assertEqual(compact['onsets'],[[4.1,.8,-12]])
        self.assertEqual(compact['energy_by_second'],[[4,5,-20,-10]])

    def test_observed_pulse_alignment_preserves_action_and_rejects_outside_tolerance(self):
        plan={'music_asset_id':'m','music_start':2,'clips':[
            {'asset_id':'v','start':3,'end':5,'speed':1,'beat_sync':True},
            {'asset_id':'v','start':7,'end':9,'speed':1,'beat_sync':False},
            {'asset_id':'v','start':10,'end':12,'speed':1,'beat_sync':True}]}
        evidence={'audio':{'m':{'beat_candidates':[{'time':4.1},{'time':6.1},{'time':8.5}]}}}
        actual=align_beats(copy.deepcopy(plan),evidence)
        self.assertEqual([(c['start'],c['end']) for c in actual['clips']],[(3,5),(7,9),(10,12)])
        self.assertAlmostEqual(actual['clips'][0]['duration'],2.1)
        self.assertIsNone(actual['clips'][1]['beat_alignment'])
        self.assertIsNone(actual['clips'][2]['beat_alignment'])
        assets=[{'id':'v','kind':'video','duration':15,'has_audio':True},{'id':'m','kind':'audio','duration':20,'has_audio':True}]
        checked=validate_plan(actual,assets)
        self.assertAlmostEqual(checked['clips'][0]['speed'],2/2.1)
        bad=copy.deepcopy(actual);bad['clips'][0]['speed']=0
        with self.assertRaises(ValueError):validate_plan(bad,assets)

    def test_offscreen_peak_is_rejected_and_visible_tail_is_trimmed(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(db,'DATA',Path(temp)), patch('action_editing.sheets',return_value=[]):
            directory=Path(temp)/'frames/action-editing/v1-test';directory.mkdir(parents=True)
            base={'start':0,'end':4,'description':'动作','confidence':'high'}
            evidence={'source_sha256':'test','actions':[{**base,'peak':3.5},{**base,'peak':1}]}
            labels=[True,True,True,True,False,False,False,False,False]
            with patch('providers._json_response',return_value={'observations':[
                {'index':0,'visible':labels,'reason':'后半段主体出画'},
                {'index':1,'visible':labels,'reason':'后半段主体出画'}]}):
                result=visible_actions({'id':'video','path':'unused'},evidence)
            self.assertEqual(len(result['actions']),1)
            self.assertEqual(len(result['rejected_actions']),1)
            self.assertEqual(result['actions'][0]['peak'],1)
            self.assertAlmostEqual(result['actions'][0]['end'],1.65)
