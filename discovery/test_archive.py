"""Long-term archival is reversible and blocked by uncertain/common-mode failures."""
import copy
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import media_fingerprint, recent_checks
from playlist_update import apply, archive_review, archived_entries, parse, plan
from server import Worker
import router


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 10, 10, 30, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        self.keys = [f"239.255.42.{i}:8000" for i in range(1, 11)]
        self.base = '#EXTM3U x-tvg-url="kept"\n' + ''.join(
            f'#EXTINF:-1 tvg-id="id-{i}" group-title="Example",Example {i}\nhttp://192.0.2.1:12123/udp/{key}\n'
            for i, key in enumerate(self.keys))
        self.logo = self.base.replace('group-title="Example"', 'group-title="Example" tvg-logo="http://192.0.2.1:8080/iptv/logos/example.png"')
        self.rows = [self.media(i) for i in range(10)]
        self.rows[3] = {"endpoint":self.keys[3],"validation":"no_response", "checked_at":self.now,
                        "last_failure":self.now,"archive_failure_checks":[self.now-9*86400,self.now-4*86400,self.now]}
        self.audit = {"state":"complete","total":10,"done":10,"finished":self.now}

    def media(self, index):
        row = {"endpoint":self.keys[index],"validation":"media","checked_at":self.now,
               "last_success":self.now,"protocol":"udp","successes":2,
               "success_checks":[self.now-120,self.now], "programs":[{"id":"1","name":f"Example {index}"}],
               "streams":[{"type":"video","codec":"h264"}]}
        row['success_fingerprint'] = media_fingerprint(row['endpoint'], row)
        return row

    def review(self, **fields):
        return archive_review(parse(self.base)[1], fields.get('rows',self.rows), fields.get('audit',self.audit),
                              fields.get('link_ready',True), self.now)

    def test_gate_requires_complete_recent_coverage_and_healthy_control(self):
        self.assertEqual(self.review()['eligible'], [self.keys[3]])
        for audit in ({}, {**self.audit,'state':'paused'}, {**self.audit,'done':9},
                      {**self.audit,'finished':self.now-90000}, {**self.audit,'finished':self.now+1}):
            self.assertEqual(self.review(audit=audit)['state'],'blocked')
        self.assertEqual(self.review(link_ready=False)['state'],'blocked')
        self.assertEqual(self.review(rows=self.rows[1:])['state'],'blocked')
        stale=copy.deepcopy(self.rows);stale[0]['checked_at']=self.now-90000
        self.assertEqual(self.review(rows=stale)['state'],'blocked')
        uncertain=copy.deepcopy(self.rows);uncertain[0]['last_check']='inconclusive'
        self.assertEqual(self.review(rows=uncertain)['state'],'blocked')
        failed=copy.deepcopy(self.rows)
        for row in failed[:3]:row['last_check']='no_response'
        self.assertIn('20%',self.review(rows=failed)['reason'])

    def test_short_night_same_day_legacy_and_recovered_evidence_cannot_archive(self):
        for checks in ([], [self.now-6*86400,self.now-3*86400,self.now],
                       [self.now-7200,self.now-3600,self.now],
                       [self.now-9*86400-7*3600,self.now-4*86400-7*3600,self.now-7*3600],
                       [self.now-9*86400,self.now-4*86400,self.now+1]):
            rows=copy.deepcopy(self.rows);rows[3]['archive_failure_checks']=checks
            self.assertEqual(self.review(rows=rows)['eligible'],[])
        rows=copy.deepcopy(self.rows);rows[3]['last_success']=self.now-2*86400
        self.assertEqual(self.review(rows=rows)['eligible'],[])
        rows=copy.deepcopy(self.rows)
        rows[3]['daytime_failure_checks']=rows[3].pop('archive_failure_checks')
        self.assertEqual(self.review(rows=rows)['eligible'],[])

    def test_qualified_history_only_from_complete_healthy_unchanged_link_rounds(self):
        with tempfile.TemporaryDirectory() as temp, patch('server.time.time',return_value=self.now):
            worker=Worker(Path(temp)/'state.json','192.0.2.1')
            worker.state['baseline']=[{'endpoint':k} for k in self.keys]
            observations={r['endpoint']:copy.deepcopy(r) for r in self.rows}
            observations[self.keys[3]].pop('archive_failure_checks')
            worker.state['observations']=observations
            worker.state['audit']={**self.audit,'started':self.now-100,'generation':'test-generation'}
            worker.link={'ready':True,'generation':'test-generation'}
            with patch.object(worker,'link_ready',return_value=True):
                worker.record_archive_evidence()
                self.assertEqual(observations[self.keys[3]]['archive_failure_checks'],[self.now])
                worker.record_archive_evidence()
                self.assertEqual(observations[self.keys[3]]['archive_failure_checks'],[self.now])
                for field,value in [('state','paused'),('done',9),('generation','other'),('started',self.now-90000)]:
                    old=worker.state['audit'][field];worker.state['audit'][field]=value
                    observations[self.keys[3]]['archive_failure_checks']=[]
                    worker.record_archive_evidence()
                    self.assertEqual(observations[self.keys[3]]['archive_failure_checks'],[])
                    worker.state['audit'][field]=old
                for row in list(observations.values())[:3]: row['last_check']='no_response'
                worker.record_archive_evidence()
                self.assertEqual(observations[self.keys[3]]['archive_failure_checks'],[])

    def test_generic_failure_history_survives_a_week_but_never_qualifies_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            worker=Worker(Path(temp)/'state.json','192.0.2.1');key=self.keys[3]
            for stamp in [self.now-9*86400,self.now-4*86400,self.now]:
                with patch('server.time.time',return_value=stamp): worker.observe(key,{'validation':'no_response'})
            self.assertEqual(len(worker.state['observations'][key]['daytime_failure_checks']),3)
            self.assertFalse(worker.state['observations'][key].get('archive_failure_checks'))
            worker.state['observations'][key]['archive_failure_checks']=[self.now]
            with patch('server.time.time',return_value=self.now+1): worker.observe(key,{'validation':'inconclusive'})
            self.assertEqual(worker.state['observations'][key]['archive_failure_checks'],[])

    def test_archive_window_accepts_evening_but_excludes_early_and_late_checks(self):
        for hour, minute, allowed in [(8,59,False),(9,0,True),(17,59,True),
                                      (18,30,True),(19,0,True),(21,59,True),(22,0,False)]:
            with self.subTest(hour=hour, minute=minute):
                stamp=dt.datetime(2026,11,10,hour,minute,tzinfo=router.SHANGHAI).timestamp()
                rows=copy.deepcopy(self.rows)
                for row in rows:row['checked_at']=stamp
                rows[3].update(last_failure=stamp,archive_failure_checks=[stamp-9*86400,stamp-4*86400,stamp])
                audit={**self.audit,'started':stamp-100,'finished':stamp,'generation':'window-test'}
                result=archive_review(parse(self.base)[1],rows,audit,True,stamp)
                self.assertEqual(result['eligible'],[self.keys[3]] if allowed else [])
                with tempfile.TemporaryDirectory() as temp, patch('server.time.time',return_value=stamp):
                    worker=Worker(Path(temp)/'state.json','192.0.2.1')
                    worker.state['baseline']=[{'endpoint':key} for key in self.keys]
                    worker.state['observations']={row['endpoint']:copy.deepcopy(row) for row in rows}
                    failed=worker.state['observations'][self.keys[3]]
                    failed['archive_failure_checks']=[]
                    worker.state['audit']=audit
                    worker.link={'ready':True,'generation':'window-test'}
                    with patch.object(worker,'link_ready',return_value=True):worker.record_archive_evidence()
                    self.assertEqual(failed['archive_failure_checks'],[stamp] if allowed else [])

    def test_evidence_window_override_preserves_default_filter(self):
        evening=dt.datetime(2026,11,10,19,0,tzinfo=router.SHANGHAI).timestamp()
        morning=evening-9*3600
        self.assertEqual(recent_checks([morning,evening],evening,daytime=True),[morning])
        self.assertEqual(recent_checks([morning,evening],evening,daytime=True,daytime_end=22),[morning,evening])

    def test_reverse_restore_keeps_relative_order_after_other_additions(self):
        entries=parse(self.base)[1];logo_entries=parse(self.logo)[1]
        saved={self.keys[i]:{'kind':'archive','name':entries[i]['name'],'index':i,
                            'entry':entries[i]['lines'],'logo_entry':logo_entries[i]['lines'],
                            'preceding':self.keys[:i],'following':self.keys[i+1:]} for i in (3,7)}
        base=self.base;logo=self.logo
        for i in (3,7):
            base=base.replace('\n'.join(entries[i]['lines'])+'\n','')
            logo=logo.replace('\n'.join(logo_entries[i]['lines'])+'\n','')
        files,changes,_=plan(base,logo,[self.media(7),self.media(3)],now=self.now,archived=saved)
        self.assertEqual(files['new.m3u'],self.base)
        self.assertEqual(files['new-logo.m3u'],self.logo)
        self.assertEqual([r['kind'] for r in changes],['restore','restore'])

    def test_many_eligible_entries_are_not_archived_in_bulk(self):
        rows=copy.deepcopy(self.rows);rows[4]={**rows[3],'endpoint':self.keys[4]}
        self.assertIn('5%',self.review(rows=rows)['reason'])

    def test_no_control_in_same_subnet_and_port_cannot_archive(self):
        rows=copy.deepcopy(self.rows)
        for r in rows:
            if r['validation']=='media':r['endpoint']=r['endpoint'].replace('239.255.42.', '239.255.43.')
        entries=[{'endpoint':r['endpoint']} for r in rows]
        self.assertEqual(archive_review(entries,rows,self.audit,True,self.now)['eligible'],[])

    def test_default_never_archives_and_explicit_archive_keeps_full_restore_metadata(self):
        self.assertFalse(plan(self.base,self.logo,self.rows,now=self.now)[0])
        files,changes,_=plan(self.base,self.logo,self.rows,now=self.now,auto_archive=True,audit=self.audit,link_ready=True)
        self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['kind'],'archive')
        self.assertEqual(changes[0]['index'],3)
        self.assertIn('tvg-logo=',changes[0]['logo_entry'][0])
        self.assertEqual(len(parse(files['new.m3u'])[1]),9)
        restored=[self.media(i) for i in range(10)]
        restored_files,restored_changes,_=plan(files['new.m3u'],files['new-logo.m3u'],restored,now=self.now,
                                               archived={self.keys[3]:changes[0]})
        self.assertEqual(restored_changes[0]['kind'],'restore')
        self.assertEqual(restored_files['new.m3u'],self.base)
        self.assertEqual(restored_files['new-logo.m3u'],self.logo)
        mismatch=self.media(3);mismatch['programs'][0]['name']='Different'
        mismatch['success_fingerprint']=media_fingerprint(self.keys[3],mismatch)
        self.assertFalse(plan(files['new.m3u'],files['new-logo.m3u'],[mismatch],now=self.now,
                              archived={self.keys[3]:changes[0]})[0])

    def test_archive_commit_and_restore_are_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp, patch('playlist_update.time.time',return_value=self.now):
            root=Path(temp);www=root/'www';www.mkdir();backups=root/'backups'
            for name,value in [('new.m3u',self.base),('new-logo.m3u',self.logo),('new.txt','original txt')]:
                (www/name).write_text(value)
            result=apply(www,backups,self.rows,auto_archive=True,audit=self.audit,link_ready=True)
            self.assertEqual(result['state'],'applied')
            self.assertEqual((Path(result['backup'])/'new.m3u').read_text(),self.base)
            self.assertEqual(len(archived_entries(backups)),1)
            restored=apply(www,backups,[self.media(i) for i in range(10)])
            self.assertEqual(restored['changes'][0]['kind'],'restore')
            self.assertEqual(archived_entries(backups),{})
            self.assertEqual((www/'new.m3u').read_text(),self.base)
            self.assertEqual(apply(www,backups,[self.media(i) for i in range(10)])['state'],'unchanged')

    def test_archive_setting_requires_separate_confirmation_and_preserves_old_clients(self):
        options={'enabled':True,'daily_time':'09:30','weekdays_only':True,'weekly_enabled':True,
                 'weekly_day':4,'weekly_time':'09:30','auto_update':True,'auto_archive':True,
                 'auto_process':True,'active_enabled':False,'ranges':[],'ports':[]}
        for current,consent,include,expected in [(False,False,True,False),(False,True,True,True),(True,False,False,True)]:
            supplied=dict(options)
            if not include:supplied.pop('auto_archive')
            raw=json.dumps({'user':'test','password':'test-only','action':'settings','settings':supplied,
                            'archive_consent':consent}).encode()
            output=io.StringIO()
            with patch.dict(os.environ,{'REQUEST_METHOD':'POST','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw))},clear=True), \
                    patch('router.sys.stdin') as stdin,patch('router.sys.stdout',output), \
                    patch('router.Path.read_text',return_value='test:test-only'), \
                    patch('router.config',return_value={'auto_archive':current}), \
                    patch('router.atomic_json') as save,patch('router.nas_request',return_value={}) as request:
                stdin.buffer.read.return_value=raw
                router.cgi()
            if expected:
                self.assertIn('Status: 200',output.getvalue())
                self.assertTrue(save.call_args.args[1]['auto_archive'])
            else:
                self.assertIn('Status: 403',output.getvalue())
                save.assert_not_called();request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
