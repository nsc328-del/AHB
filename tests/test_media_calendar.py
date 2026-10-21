import sys,json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import storage as db
import daily_summaries as daily
import publication_data as publication

class MediaCalendarTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.scope=patch.object(db,'DATA',Path(self.tmp.name));self.scope.start();self.addCleanup(self.scope.stop);db.initialize()
    def asset(self,**changes):
        return db.create('assets',{'kind':'video','purpose':'library','name':'测试.mp4','duration':6,
            'created_at':'2026-10-20T15:59:00+00:00','analysis_status':'succeeded',
            'analysis':{'analysis_id':'v1','summary':'厨师切菜，餐厅就餐画面','tags':['餐厅'],'segments':[]},**changes})
    def test_project_creation_duplicate_and_data_isolation(self):
        item=publication.create_workspace('餐饮测试')
        with self.assertRaises(ValueError):publication.create_workspace(' 餐饮测试 ')
        with self.assertRaises(ValueError):publication.create_workspace('星尚')
        db.create('publication_cases',{'source':{'kind':'alpha_sqlite','location':publication.SOURCE_LOCATION},'platform':'douyin','account':'a','published_at':'2026-10-20T12:00:00+08:00','metrics':{}})
        self.assertEqual(publication.history_dashboard(item['id'])['scope']['project_videos'],0)
        self.assertEqual(publication.history_dashboard()['scope']['project_videos'],1)
        self.assertEqual(len(publication.business_workspaces()['workspaces']),2)
    def test_timezone_schedule_reference_exclusion_and_inflight_dedup(self):
        asset=self.asset();self.asset(purpose='reference');self.asset(source='candidate_preview');self.asset(created_at='2026-10-20T16:01:00+00:00')
        self.assertEqual(len(daily.inventory()['2026-10-20']),1)
        self.assertEqual(len(daily.inventory()['2026-10-21']),1)
        with patch.object(daily,'queue_day') as queue:
            daily.run_due(datetime.fromisoformat('2026-10-21T00:59:00+08:00'));queue.assert_not_called()
            daily.run_due(datetime.fromisoformat('2026-10-21T01:00:00+08:00'));self.assertEqual(queue.call_args.args[0],'2026-10-20')
        with patch.object(daily.workflow,'_schedule_job') as schedule:
            first=daily.queue_day('2026-10-20');second=daily.queue_day('2026-10-20')
            self.assertEqual(first['job_id'],second['job_id']);self.assertEqual(schedule.call_count,1)
    def test_summary_reuse_changed_evidence_and_pending_analysis(self):
        asset=self.asset();response={'summary':'餐厅烹饪和就餐素材','themes':['餐厅'],'combinations':[{'idea':'烹饪短片','asset_ids':[asset['id']]}]}
        with patch.object(daily.workflow,'_schedule_job') as schedule,patch.object(daily.providers,'_json_response',return_value=response) as model:
            first=daily.queue_day('2026-10-20');operation=schedule.call_args.args[1];operation(first['job_id']);db.update('jobs',first['job_id'],{'status':'succeeded'})
            self.assertTrue(daily.queue_day('2026-10-20')['reused']);self.assertEqual(model.call_count,1)
            db.update('assets',asset['id'],{'analysis_status':'running'})
            self.assertEqual(daily.queue_day('2026-10-20')['status'],'waiting_analysis')
            db.update('assets',asset['id'],{'analysis_status':'succeeded','analysis':{'analysis_id':'v2','summary':'新分析'}})
            second=daily.queue_day('2026-10-20');self.assertNotEqual(first['job_id'],second['job_id'])
            self.assertEqual(db.get('daily_summaries','2026-10-20')['previous_result']['summary'],response['summary'])

if __name__=='__main__':unittest.main()
