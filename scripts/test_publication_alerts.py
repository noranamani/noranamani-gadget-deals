"""Read-only schedule gate and once-per-day GitHub alert regressions (offline)."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import test_windows_pipeline as fixtures
from pipeline_common import JST, PipelineError
from publication_gate import scheduled_gate, main as publication_main, output_decision, DEPLOY_STEP
from publication_daily_alert import alert_reason, issue_title, notify_once, main as alert_main


NOW = fixtures.SUNDAY  # Matches the fixed affiliate proof and product fixture
RUN_ID = '41812224400'


def reader(*, published=False, ambiguous=False):
    def get(path):
        if '/runs?' in path:
            return {'workflow_runs': [{
                'id': 400, 'head_branch': 'main', 'created_at': NOW.isoformat()}]}
        if '/jobs?' in path:
            conclusion = 'failure' if ambiguous else 'success' if published else 'skipped'
            return {'jobs': [{
                'name': 'deploy', 'steps': [{'name': DEPLOY_STEP, 'conclusion': conclusion}]}]}
        if path.startswith('actions/runs/'):
            return {'created_at': NOW.isoformat()}
        raise AssertionError('unrecognized read path')
    return Mock(side_effect=get)


class ScheduledGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublicationTests('test_matching_generation_ready')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root

    def test_fresh_and_no_deploy_is_ready(self):
        self.assertEqual(scheduled_gate(self.root, NOW, reader()), 'ready')

    def test_successful_real_pages_receipt_skips_even_after_products_expire(self):
        get = reader(published=True)
        result = scheduled_gate(self.root, NOW + timedelta(hours=4), get)
        self.assertEqual(result, 'already_published')
        self.assertTrue(any('/jobs?' in x.args[0] for x in get.call_args_list))

    def test_unpublished_stale_product_is_blocked_not_publishable(self):
        get = reader()
        with self.assertRaisesRegex(PipelineError, 'PRODUCTS_STALE_OR_FUTURE'):
            scheduled_gate(self.root, NOW + timedelta(hours=4), get)

    def test_unknown_or_failed_pages_click_never_counted_as_safe_skip(self):
        get = reader(ambiguous=True)
        with self.assertRaisesRegex(PipelineError, 'DEPLOYMENT_UNCERTAIN'):
            scheduled_gate(self.root, NOW, get)

    def test_manual_dispatch_remains_strict_and_has_no_soft_failure(self):
        # Scheduled mode explicitly requires a schedule event. The ordinary
        # manual/Windows publishing gate remains fail-closed.
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {
                'GITHUB_REPOSITORY':'noranamani/noranamani-gadget-deals',
                'GITHUB_RUN_ID':RUN_ID,'GITHUB_EVENT_NAME':'workflow_dispatch',
                'GH_TOKEN':'fake-token','GITHUB_OUTPUT':str(Path(d)/'out')},clear=False), \
             patch('publication_gate.github_reader',return_value=reader()), \
             patch('sys.argv',['publication_gate.py','--root',str(self.root),
                               '--site-only','--scheduled-check']), \
             patch('publication_gate.datetime') as clock:
            clock.now.return_value = NOW
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(publication_main(), 1)
            self.assertIn('PUBLICATION_SCHEDULE_CONTEXT_INVALID',out.getvalue())
            self.assertFalse((Path(d)/'out').exists())

    def test_scheduled_stale_yields_only_blocked_no_publish(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {
                'GITHUB_REPOSITORY':'noranamani/noranamani-gadget-deals',
                'GITHUB_RUN_ID':RUN_ID,'GITHUB_EVENT_NAME':'schedule',
                'GH_TOKEN':'fake-token','GITHUB_OUTPUT':str(Path(d)/'out')},clear=False), \
             patch('publication_gate.github_reader',return_value=reader()), \
             patch('publication_gate.scheduled_gate',side_effect=PipelineError(
                  'PRODUCTS_STALE_OR_FUTURE')), \
             patch('sys.argv',['publication_gate.py','--root',str(self.root),
                               '--site-only','--scheduled-check']), \
             patch('publication_gate.datetime') as clock:
            clock.now.return_value = NOW
            self.assertEqual(publication_main(),0)
            out=(Path(d)/'out').read_text(encoding='utf-8')
            self.assertIn('publish=false',out)
            self.assertIn('gate_status=blocked',out)
            self.assertIn('gate_reason=PRODUCTS_STALE_OR_FUTURE',out)
            self.assertNotIn('publish=true',out)

    def test_untrusted_codes_cannot_pollute_github_output(self):
        with self.assertRaisesRegex(PipelineError,'REASON_INVALID'):
            output_decision(False,'blocked','URL_PRIVATE_TOKEN=secret')
        with self.assertRaisesRegex(PipelineError,'DECISION_INVALID'):
            output_decision(True,'unknown','NONE')


class DailyIssueTests(unittest.TestCase):
    def test_reason_classification_separates_real_success_and_stale(self):
        self.assertIsNone(alert_reason('success','skipped','skipped','already_published',
                                       'NONE','false'))
        self.assertEqual(alert_reason('success','skipped','skipped','blocked',
                                       'PRODUCTS_STALE_OR_FUTURE','false'),
                         'PRODUCTS_STALE_OR_FUTURE')
        self.assertEqual(alert_reason('success','failure','skipped','ready',
                                       'NONE','true'),'DEPLOY_NOT_VERIFIED')
        self.assertEqual(alert_reason('success','success','failure','ready',
                                       'NONE','true'),'WEEKLY_NOTE_MATERIALS_FAILED')

    def test_first_failure_creates_one_issue_and_closed_issue_also_suppresses_next(self):
        calls=[]
        def request(method,route,body,token):
            calls.append((method,route,body,token))
            if method=='GET':
                return []
            return {'number':56}
        r=notify_once(NOW,run_id=RUN_ID,reason='PRODUCTS_STALE_OR_FUTURE',
                      token='test-token',request=request)
        self.assertEqual(r['status'],'CREATED')
        self.assertEqual(len(calls),2)
        self.assertIn('/issues?',calls[0][1])
        self.assertIn('actions/runs/'+RUN_ID,calls[1][2]['body'])
        self.assertNotIn('test-token',calls[1][2]['body'])
        self.assertNotIn('GITHUB_TOKEN',calls[1][2]['body'])
        def already(method,route,body,token):
            self.assertEqual(method,'GET')
            return [{'title':issue_title(NOW),'number':56,'state':'closed'}]
        r=notify_once(NOW,run_id=RUN_ID,reason='PREPARE_FAILED',
                      token='test-token',request=already)
        self.assertEqual(r['status'],'ALREADY_REPORTED')

    def test_repeated_runs_and_wrong_repository_never_create_unbounded_issue(self):
        with self.assertRaisesRegex(PipelineError,'CONTEXT_INVALID'):
            notify_once(NOW,run_id='abc',reason='PREPARE_FAILED',token='x',
                        request=Mock())
        with self.assertRaisesRegex(PipelineError,'PAGINATION_LIMIT'):
            notify_once(NOW,run_id=RUN_ID,reason='PREPARE_FAILED',
                        token='test',request=Mock(return_value=[
                            {'number':i,'title':'something else'} for i in range(100)]))

    def test_recovery_window_defers_unverified_alert_without_one_per_cron_spam(self):
        base={'GITHUB_REPOSITORY':'noranamani/noranamani-gadget-deals',
              'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'schedule',
              'PREPARE_RESULT':'success','GATE_STATUS':'blocked',
              'GATE_REASON':'PRODUCTS_STALE_OR_FUTURE','PUBLISH_NEEDED':'false',
              'GITHUB_RUN_ID':RUN_ID,'GH_TOKEN':'test-token'}
        with patch.dict(os.environ,base), \
             patch('publication_daily_alert.datetime') as clock, \
             patch('publication_daily_alert.notify_once') as notify:
            clock.now.return_value=NOW.replace(hour=8,minute=30)
            self.assertEqual(alert_main(),0)
            notify.assert_not_called()
            clock.now.return_value=NOW.replace(hour=11,minute=30)
            notify.return_value={'status':'CREATED'}
            self.assertEqual(alert_main(),1)
            clock.now.return_value=NOW.replace(hour=11,minute=59)
            notify.return_value={'status':'ALREADY_REPORTED'}
            self.assertEqual(alert_main(),0)
            self.assertEqual(notify.call_count,2)

    def test_workflow_uses_soft_gate_only_before_prepare_and_manual_is_strict(self):
        root=Path(__file__).resolve().parents[1]
        y=(root/'.github/workflows/windows-daily-publish.yml').read_text()
        a=y[:y.index('  deploy:')]
        b=y[y.index('  deploy:'):y.index('  report:')]
        self.assertIn('--scheduled-check',a)
        self.assertNotIn('--scheduled-check',b)
        self.assertIn('issues: write',y[y.index('  alert:'):])
        self.assertNotIn('issues: write',y[:y.index('  alert:')])
        self.assertIn("github.event_name == 'schedule'",y[y.index('  alert:'):])
        self.assertIn('publication_daily_alert.py',y)
        self.assertIn("needs.prepare.outputs.gate_status != 'blocked'",y)


if __name__=='__main__':
    unittest.main()
