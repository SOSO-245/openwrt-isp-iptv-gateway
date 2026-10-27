import datetime as dt
import json
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from common import differences, endpoint, media_fingerprint, multicast_ranges, packet_endpoints, playlist, recent_checks
from router import due, due_weekly, cgi, validate_schedule, udpxy_master, catalog, next_runs
from server import Worker, active_clients, BoundedServer, Handler
from playlist_update import plan, apply, parse, identity, eligible

BASE = [{"endpoint": "239.10.1.1:8000", "protocol": "udp", "name": "CCTV-1", "channel_id": "cctv1"}]


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.worker = Worker(Path(self.tmp.name) / 'state.json', '192.0.2.1')
        self.worker.ingest({"baseline": BASE, "observations": []})
        self.worker.set_link({"ready": True, "generation": "test"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_scope(self):
        for value in ('127.0.0.1', '192.0.2.1', '224.0.0.1', '239.256.0.1'):
            with self.assertRaises(ValueError):
                endpoint(value, 80)
        for value in ('239.10.0.0/16', '239.11.1.0/24', 'http://example.com', '::/0'):
            with self.assertRaises(ValueError):
                multicast_ranges([value], BASE)
        self.assertEqual(str(multicast_ranges(['239.10.1.0/28'], BASE)[0]), '239.10.1.0/28')

    def test_packet_metadata_only(self):
        rows = packet_endpoints('01:02:03 IP 192.0.2.100.123 > 239.10.1.2.8000: UDP, length 1316\n'
                                'IP 192.0.2.1.123 > 192.0.2.2.80: UDP, length 4\n')
        self.assertEqual(rows, [{"endpoint": "239.10.1.2:8000", "packets": 1}])

    def test_playlist(self):
        rows = playlist('#EXTM3U\n#EXTINF:-1 tvg-id="one",CCTV-1\nhttp://router:12123/udp/239.10.1.1:8000\n')
        self.assertEqual(rows[0]['channel_id'], 'one')
        self.assertEqual(rows[0]['endpoint'], BASE[0]['endpoint'])
        self.assertEqual(playlist('http://router:12123/udp/127.0.0.1:80'), [])

    def test_no_packets_not_offline(self):
        self.assertEqual(self.worker.status()['rows'], [])
        self.assertEqual(self.worker.status()['baseline_count'], 1)
        self.assertEqual(self.worker.export()['playlist'], '#EXTM3U\n')

    def test_move_is_only_suspicion(self):
        observed = {'239.10.1.2:8000': {'validation':'media', 'programs':[{'name':'CCTV-1'}]}}
        diff = differences(BASE, observed)[0]
        self.assertEqual(diff['change'], 'possible_move')
        self.assertEqual(diff['previous'], ['239.10.1.1:8000'])
        observed['239.10.1.2:8000']['validation'] = 'pending'
        self.assertEqual(differences(BASE, observed)[0]['change'], 'unverified')

    def test_daily_once_shanghai(self):
        now = dt.datetime(2026,10,26,3,30,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        self.assertTrue(due(now, '', '03:30'))
        self.assertFalse(due(now, '2026-10-26', '03:30'))
        self.assertFalse(due(now + 60, '', '03:30'))

    def test_friday_once(self):
        now = dt.datetime(2026,10,30,3,30,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        self.assertTrue(due_weekly(now, '', 4, '03:30'))
        self.assertFalse(due_weekly(now, '2026-10-30', 4, '03:30'))
        self.assertFalse(due_weekly(now + 86400, '', 4, '03:30'))
        with self.assertRaises(ValueError):
            validate_schedule({'weekly_enabled':True,'auto_update':True,'weekly_day':7,'weekly_time':'03:30'})

    def test_weekday_morning(self):
        monday = dt.datetime(2026,10,26,9,30,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        for day in range(7):
            self.assertEqual(due(monday + day * 86400, '', '09:30', True), day < 5)
        self.assertFalse(due(monday, '2026-10-26', '09:30', True))
        self.assertFalse(due(monday - 6 * 3600, '', '09:30', True))
        self.assertTrue(due_weekly(monday + 4 * 86400, '', 4, '09:30'))

    def test_friday_audit_replaces_daily_sample(self):
        import router
        now=dt.datetime(2026,10,30,9,30,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        settings={'enabled':True,'daily_time':'09:30','weekdays_only':True,
                  'weekly_enabled':True,'weekly_day':4,'weekly_time':'09:30'}
        def request(_config,path,_data):
            if path=='/v1/audit': router.STOP=True
            return {}
        with patch('router.STOP',False),patch('router.signal.signal'),patch('router.config',return_value=settings),patch('router.time.time',return_value=now),patch('router.link_status',return_value={}),patch('router.STAMP',Path(self.tmp.name)/'schedule.json'),patch('router.RUNTIME',Path(self.tmp.name)/'runtime'),patch('router.nas_request',side_effect=request) as api,patch('router.baseline_report',return_value={}),patch('router.sample') as sample,patch('router.reconcile'):
            router.daemon()
            sample.assert_not_called()
            self.assertEqual([c.args[1] for c in api.call_args_list].count('/v1/audit'),1)
            self.assertEqual(next(c.args[2] for c in api.call_args_list if c.args[1]=='/v1/audit'), {'trigger':'scheduled'})

    def test_confirmation_cannot_revive_after_identity_change(self):
        key,item=self.unnamed()
        self.worker.confirm_name({'endpoint':key,'fingerprint':media_fingerprint(key,item),'name':'地方频道','reviewed':True})
        original={k:item[k] for k in ('validation','protocol','programs','streams')}
        self.worker.observe(key,dict(original,programs=[{'id':'2','name':''}]))
        self.worker.observe(key,original)
        self.assertTrue(self.worker.rows()[0]['confirmation_stale'])
        self.assertIsNone(eligible(self.worker.rows()[0],time.time()))

    def unnamed(self):
        key='239.10.1.2:8000'
        item={'validation':'media','protocol':'udp','programs':[],
              'streams':[{'type':'video','codec':'h264'}],'successes':2,
              'first_success':time.time()-120,'last_success':time.time()}
        item.update(success_checks=[item['first_success'],item['last_success']],success_fingerprint=media_fingerprint(key,item))
        self.worker.state['observations'][key]=item
        return key,item

    def test_catalog_is_only_suggestion(self):
        key,item=self.unnamed()
        self.worker.ingest({'baseline':BASE,'catalog':{'at':1000,'rows':[dict(BASE[0],endpoint=key)]}})
        row=self.worker.rows()[0]
        self.assertEqual(row['name_source'],'catalog')
        self.assertEqual(row['name'],'CCTV-1')
        self.assertIsNone(eligible(row,time.time()))
        self.worker.state['catalog']['rows'].append(dict(BASE[0],endpoint=key,name='另一个频道'))
        row=self.worker.rows()[0]
        self.assertEqual(row['name_source'],'conflict')
        self.assertEqual(row['name'],'')
        self.worker.state['catalog']['rows']=[dict(BASE[0],endpoint=key,protocol='rtp')]
        self.assertEqual(self.worker.rows()[0]['name_source'],'unknown')

    def test_confirmation_persists_revokes_and_invalidates(self):
        key,item=self.unnamed()
        data={'endpoint':key,'fingerprint':media_fingerprint(key,item),'name':'地方频道','reviewed':True}
        self.worker.confirm_name(data)
        self.assertEqual(eligible(self.worker.rows()[0],time.time()),'地方频道')
        restored=Worker(self.worker.path,self.worker.router)
        self.assertEqual(restored.rows()[0]['name_source'],'manual')
        restored.state['observations'][key]['programs']=[{'id':'2','name':''}]
        self.assertTrue(restored.rows()[0]['confirmation_stale'])
        self.assertIsNone(eligible(restored.rows()[0],time.time()))
        with self.assertRaises(ValueError): restored.confirm_name(data)
        self.worker.confirm_name(dict(data,revoke=True))
        self.assertEqual(self.worker.rows()[0]['name_source'],'unknown')
        self.assertIsNone(eligible(self.worker.rows()[0],time.time()))

    def test_confirmation_safety(self):
        key,item=self.unnamed()
        data={'endpoint':key,'fingerprint':media_fingerprint(key,item),'name':'地方频道','reviewed':True}
        for update in ({'reviewed':False},{'name':'bad\nname'},{'name':'频道1'},{'name':'bad"name'},
                       {'fingerprint':'old'},{'endpoint':BASE[0]['endpoint']}):
            with self.assertRaises(ValueError): self.worker.confirm_name(dict(data,**update))
        self.worker.job_lock.acquire()
        with self.assertRaises(ValueError): self.worker.confirm_name(data)
        self.worker.job_lock.release()
        item['last_check']='no_response'
        with self.assertRaises(ValueError): self.worker.confirm_name(data)
        item.pop('last_check');item['last_success']=time.time()-8*86400
        with self.assertRaises(ValueError): self.worker.confirm_name(data)

    def test_deep_target_scope(self):
        for targets in ([], ['127.0.0.1:80'], [BASE[0]['endpoint']]*2):
            with self.assertRaises(ValueError): self.worker.start('identify',{'endpoints':targets})
            self.assertFalse(self.worker.job_lock.locked())

    def test_deep_probe_bounds(self):
        key,item=self.unnamed();item['protocol']='rtp'
        with patch('server.http.client.HTTPConnection') as connect, patch('server.media_details',return_value={'validation':'media'}) as analyze:
            response=connect.return_value.getresponse.return_value
            response.status=200;response.read1.side_effect=lambda n:b'x'*n
            result=self.worker.probe(key,deep=True)
            self.assertEqual(result['bytes_checked'],8388608)
            self.assertTrue(analyze.call_args.kwargs['deep'])
            connect.return_value.request.assert_called_once_with('GET','/rtp/'+key)
            connect.return_value.close.assert_called_once()

    def test_probe_uses_one_receive_deadline_for_headers_and_body(self):
        for deep,budget in ((False,4),(True,10)):
            with self.subTest(deep=deep), patch('server.http.client.HTTPConnection') as connect, \
                    patch('server.media_details',return_value={'validation':'media'}) as analyze:
                clock=[0.0];conn=connect.return_value;response=conn.getresponse.return_value
                response.status=200
                def advance(value, result=None):
                    clock[0]=value
                    return result
                conn.connect.side_effect=lambda:advance(.25)
                conn.request.side_effect=lambda *args:advance(.5)
                conn.getresponse.side_effect=lambda:advance(budget-.25,response)
                response.read1.side_effect=lambda n:advance(budget+.01,b'data')
                with patch('server.time.monotonic',side_effect=lambda:clock[0]):
                    result=self.worker.probe(BASE[0]['endpoint'],deep=deep)
                self.assertEqual([c.args[0] for c in conn.sock.settimeout.call_args_list],
                                 [budget-.25,budget-.5,.25])
                self.assertEqual(response.read1.call_count,1)
                self.assertEqual(result['validation'],'media')
                self.assertEqual(result['last_probe']['reason'],'timeout')
                self.assertEqual(analyze.call_args.args[0],b'data')
                response.close.assert_called_once();conn.close.assert_called_once()

    def test_probe_transport_errors_do_not_count_as_silent_channels(self):
        for stage in ('connect','request','headers','body'):
            with self.subTest(stage=stage), patch('server.http.client.HTTPConnection') as connect:
                conn=connect.return_value;response=conn.getresponse.return_value;response.status=200
                target={'connect':conn.connect,'request':conn.request,'headers':conn.getresponse,'body':response.read1}[stage]
                target.side_effect=ConnectionResetError('sensitive diagnostic must not escape')
                result=self.worker.probe(BASE[0]['endpoint'])
                self.assertEqual(result['validation'],'inconclusive')
                self.assertEqual(result['last_probe']['stage'],stage)
                self.assertEqual(result['last_probe']['reason'],'transport_error')
                self.assertNotIn('sensitive',json.dumps(result))
                self.worker.observe(BASE[0]['endpoint'],result)
                self.assertEqual(self.worker.state['observations'][BASE[0]['endpoint']]['daytime_failure_checks'],[])
                conn.close.assert_called_once()

    def test_probe_timeout_classifies_phase_without_resetting_budget(self):
        for stage in ('connect','request','headers','body'):
            with self.subTest(stage=stage), patch('server.http.client.HTTPConnection') as connect:
                conn=connect.return_value;response=conn.getresponse.return_value;response.status=200
                target={'connect':conn.connect,'request':conn.request,'headers':conn.getresponse,'body':response.read1}[stage]
                target.side_effect=TimeoutError('private detail')
                result=self.worker.probe(BASE[0]['endpoint'])
                self.assertEqual(result['validation'],'no_response' if stage in ('headers','body') else 'inconclusive')
                self.assertEqual(result['last_probe']['stage'],stage)
                self.assertEqual(result['last_probe']['bytes_checked'],0)
                self.assertNotIn('private detail',json.dumps(result))

    def test_latest_probe_metadata_replaces_stale_success_diagnostic(self):
        key=BASE[0]['endpoint']
        with patch('server.http.client.HTTPConnection') as connect:
            connect.return_value.getresponse.side_effect=TimeoutError()
            failure=self.worker.probe(key)
        self.worker.observe(key,{'validation':'media','last_probe':{'reason':'byte_limit'}})
        self.worker.observe(key,failure)
        item=self.worker.state['observations'][key]
        self.assertEqual(item['validation'],'media')
        self.assertEqual(item['last_check'],'no_response')
        self.assertEqual(item['last_probe']['stage'],'headers')
        self.worker.observe(key,{'validation':'inconclusive'})
        self.assertNotIn('last_probe',item)

    def test_local_catalog_restricts_metadata(self):
        path=Path(self.tmp.name)/'vsp.m3u'
        self.assertEqual(catalog(path),{'rows':[],'at':0})
        path.write_text('#EXTM3U\n#EXTINF:-1 tvg-id="private-id",频道甲\nhttp://router:12123/udp/239.10.1.2:8000\n')
        data=catalog(path)
        self.assertEqual(set(data['rows'][0]),{'endpoint','protocol','name'})
        self.assertNotIn('private-id',json.dumps(data))

    def test_playback_status(self):
        html = '<table><tr><th>Server Process ID</th><th>Active clients</th></tr><tr><td>7762</td><td>0</td></tr></table>'
        self.assertEqual(active_clients(html), 0)
        with self.assertRaises(ValueError):
            active_clients('<html>error</html>')

    def test_master_identity_ignores_forked_client(self):
        proc = Path(self.tmp.name) / 'proc'
        for pid, parent in [('123', '7762'), ('7762', '1')]:
            folder = proc / pid
            folder.mkdir(parents=True)
            (folder / 'cmdline').write_bytes(b'\0'.join([b'/usr/bin/udpxy', b'-p', b'12123', b'-m', b'192.0.2.100', b'']))
            (folder / 'status').write_text('PPid:\t' + parent + '\n')
        self.assertEqual(udpxy_master(proc), ('7762', '192.0.2.100'))

    def test_existing_name_without_stream_metadata(self):
        rows = differences(BASE, {BASE[0]['endpoint']: {'validation':'media', 'programs':[]}})
        self.assertEqual(rows[0]['name'], 'CCTV-1')
        rows = differences(BASE, {'239.10.1.2:8000': {'validation':'media', 'programs':[]}})
        self.assertEqual(rows[0]['name'], '')

    def test_busy_never_probes(self):
        with patch.object(self.worker, 'busy', return_value=True), patch.object(self.worker, 'probe') as probe:
            self.worker.start('verify', {'endpoints':[BASE[0]['endpoint']]})
            for _ in range(100):
                if self.worker.job_lock.acquire(blocking=False):
                    self.worker.job_lock.release()
                    break
                time.sleep(.01)
            probe.assert_not_called()
            self.assertEqual(self.worker.job['state'], 'paused')

    def test_only_known_verify(self):
        with self.assertRaises(ValueError):
            self.worker.start('verify', {'endpoints':['127.0.0.1:80']})
        self.assertFalse(self.worker.job_lock.locked())

    def test_scan_bounds_and_history(self):
        self.worker.settings({'active_enabled':False, 'ranges':['239.10.1.0/24'], 'ports':[8000]})
        with patch('server.threading.Thread') as thread:
            self.worker.start('scan', {})
            targets = thread.call_args.kwargs['args'][1]
            self.assertEqual(len(targets), 8)
            self.assertNotIn(BASE[0]['endpoint'], targets)
        self.worker.job_lock.release()
        self.worker.ingest({'baseline':BASE, 'observations':[{'endpoint':'239.10.1.2:8000','packets':3}]})
        self.assertEqual(self.worker.status()['history'][0]['changes'][0]['after']['change'], 'unverified')
        restored = Worker(self.worker.path, self.worker.router)
        self.assertEqual(restored.status()['rows'][0]['endpoint'], '239.10.1.2:8000')

    def test_api_auth(self):
        import http.client
        server = BoundedServer(('127.0.0.1',0), Handler)
        server.token, server.worker = 'x'*40, self.worker
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            conn.request('GET', '/v1/status')
            response = conn.getresponse()
            self.assertEqual(response.status,403); response.read()
            conn.request('GET', '/v1/status', headers={'Authorization':'Bearer ' + 'x'*40})
            response = conn.getresponse()
            self.assertEqual(response.status,200)
            self.assertNotIn('token', json.loads(response.read()))
            conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_cgi_json_without_custom_header(self):
        raw = json.dumps({'action':'status','user':'test','password':'test-only'}).encode()
        output = io.StringIO()
        with patch.dict(os.environ, {'REQUEST_METHOD':'POST','CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw))}, clear=True), patch('router.sys.stdin') as stdin, patch('router.sys.stdout', output), patch('router.Path.read_text', return_value='test:test-only'), patch('router.Path.exists', return_value=False), patch('router.config', return_value={'enabled':False,'daily_time':'03:30'}), patch('router.nas_request', return_value={}):
            stdin.buffer=io.BytesIO(raw)
            cgi()
        self.assertIn('Status: 200', output.getvalue())
        self.assertNotIn('test-only', output.getvalue())

    def test_cgi_rejects_form(self):
        output=io.StringIO()
        with patch.dict(os.environ, {'REQUEST_METHOD':'POST','CONTENT_TYPE':'text/plain'}, clear=True), patch('router.sys.stdout',output):
            cgi()
        self.assertIn('Status: 403',output.getvalue())

    def test_settings_wait_for_job(self):
        self.worker.job_lock.acquire()
        try:
            with self.assertRaises(ValueError):
                self.worker.settings({'active_enabled':False,'ranges':[],'ports':[]})
        finally:
            self.worker.job_lock.release()

    def test_audit_resume_and_restart(self):
        self.worker.state['baseline'] = BASE + [dict(BASE[0], endpoint='239.10.1.2:8000')]
        with patch('server.threading.Thread'):
            self.worker.start('audit', {})
        self.worker.state['audit'].update(done=1, state='running')
        self.worker.save()
        self.worker.job_lock.release()
        restored=Worker(self.worker.path, self.worker.router)
        self.assertEqual(restored.status()['audit']['state'], 'paused')
        self.assertNotIn('targets', restored.status()['audit'])
        with patch('server.threading.Thread') as thread:
            restored.start('audit', {})
            self.assertEqual(restored.job['done'], 1)
            self.assertEqual(len(thread.call_args.kwargs['args'][1]), 2)
        restored.job_lock.release()

    def test_link_change_does_not_mark_failure(self):
        self.worker.job={'state':'running','kind':'verify','total':1,'done':0,'started':time.time()}
        self.worker.job_lock.acquire()
        def changed(_key):
            self.worker.set_link({'ready':False,'generation':'changed'})
            return {'validation':'no_response'}
        with patch.object(self.worker,'busy',return_value=False), patch.object(self.worker,'probe',side_effect=changed):
            self.worker.run('verify',[BASE[0]['endpoint']])
        self.assertEqual(self.worker.job['state'],'paused')
        self.assertEqual(self.worker.state['observations'],{})

    def test_observation_requires_distinct_success(self):
        key=BASE[0]['endpoint']; good={'validation':'media','programs':[{'id':'1','name':'CCTV-1'}]}
        with patch('server.time.time',return_value=100000): self.worker.observe(key,good)
        with patch('server.time.time',return_value=100001): self.worker.observe(key,good)
        self.assertEqual(self.worker.state['observations'][key]['successes'],1)
        with patch('server.time.time',return_value=100061): self.worker.observe(key,good)
        self.assertEqual(self.worker.state['observations'][key]['successes'],2)
        with patch('server.time.time',return_value=100062): self.worker.observe(key,{'validation':'no_response'})
        self.assertEqual(self.worker.state['observations'][key]['validation'],'media')
        with patch('server.time.time',return_value=100063): self.worker.observe(key,good)
        self.assertNotIn('last_check',self.worker.state['observations'][key])

    def test_probe_connection_drain(self):
        with patch.object(self.worker,'busy',side_effect=[True,True,False]), patch.object(self.worker.cancel,'wait',return_value=False):
            self.assertTrue(self.worker.drain())
        with patch.object(self.worker,'busy',return_value=True), patch.object(self.worker.cancel,'wait',return_value=False):
            self.assertFalse(self.worker.drain())

    def test_success_evidence_resets_and_counts_spaced_checks(self):
        key='239.10.1.2:8000'
        good={'validation':'media','protocol':'udp','programs':[{'id':'1','name':'地方台'}],
              'streams':[{'type':'video','codec':'h264'}]}
        for stamp in (100000,100030,100060):
            with patch('server.time.time',return_value=stamp): self.worker.observe(key,good)
        item=self.worker.state['observations'][key]
        self.assertEqual(item['success_checks'],[100000,100060])
        self.assertEqual(eligible(dict(item,endpoint=key),100061),'地方台')
        with patch('server.time.time',return_value=100062): self.worker.observe(key,{'validation':'no_response'})
        with patch('server.time.time',return_value=100063): self.worker.observe(key,good)
        self.assertEqual(item['successes'],1)
        self.assertIsNone(eligible(dict(item,endpoint=key),100064))
        with patch('server.time.time',return_value=100063+8*86400): self.worker.observe(key,good)
        self.assertEqual(item['successes'],1)

    def test_protocol_or_codec_change_requires_fresh_successes(self):
        key,item=self.unnamed()
        good={k:item[k] for k in ('validation','protocol','programs','streams')}
        for update in ({'protocol':'rtp'}, {'streams':[{'type':'video','codec':'hevc'}]}):
            self.worker.observe(key,dict(good,**update))
            self.assertEqual(item['successes'],1)

    def test_daytime_evidence_not_night_failures(self):
        key=BASE[0]['endpoint']
        morning=dt.datetime(2026,10,26,9,30,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        for stamp in (morning-6*3600,morning,morning+1,morning+3600,morning+86400):
            with patch('server.time.time',return_value=stamp): self.worker.observe(key,{'validation':'no_response'})
        item=self.worker.state['observations'][key]
        self.assertEqual(item['daytime_failure_checks'],[morning,morning+3600,morning+86400])
        self.worker.observe(key,{'validation':'media'})
        self.assertEqual(item['daytime_failure_checks'],[])

    def test_recheck_oldest_four_and_previous_media_failure(self):
        for i in range(6):
            self.worker.state['observations'][f'239.10.1.{i+2}:8000']={'validation':'media','last_check':'no_response','checked_at':100+i}
        with patch('server.threading.Thread') as thread:
            self.worker.start('verify',{'retry_failed':True})
            self.assertEqual(thread.call_args.kwargs['args'][1],[f'239.10.1.{i}:8000' for i in range(2,6)])
        self.worker.job_lock.release()
        self.worker.state['observations'].clear()
        with self.assertRaises(ValueError): self.worker.start('verify',{'retry_failed':True})

    def test_verify_includes_new_media_needing_second_success(self):
        key,item=self.unnamed(); item['success_checks']=item['success_checks'][-1:]
        with patch('server.threading.Thread') as thread:
            self.worker.start('verify',{})
            self.assertEqual(thread.call_args.kwargs['args'][1],[key])
        self.worker.job_lock.release()

    def test_next_runs_weekend_same_minute_and_consumed(self):
        zone=dt.timezone(dt.timedelta(hours=8))
        c={'enabled':True,'daily_time':'09:30','weekdays_only':True,'weekly_enabled':True,'weekly_day':4,'weekly_time':'09:30'}
        friday=dt.datetime(2026,10,30,9,30,tzinfo=zone).timestamp()
        result=next_runs(c,{},friday+15)
        self.assertEqual(result,{'sample':friday+3*86400,'audit':friday})
        result=next_runs(c,{'weekly':'2026-10-30'},friday+15)
        self.assertEqual(result['audit'],friday+7*86400)
        self.assertEqual(next_runs(c,{},friday+86400)['sample'],friday+3*86400)
        self.assertEqual(next_runs(dict(c,enabled=False,weekly_enabled=False),{},friday),{})

    def test_recent_evidence_rejects_invalid_and_future_times(self):
        self.assertEqual(recent_checks([None,True,'100',{},float('nan'),float('inf'),100000,100030,100060,200000],100061),[100000,100060])


class PlaylistTests(unittest.TestCase):
    def entry(self,name,key,logo=False):
        attr=' tvg-logo="http://router:8080/iptv/logos/cctv.png"' if logo else ''
        return f'#EXTINF:-1 tvg-id="kept" group-title="原分组"{attr},{name}\nhttp://router:12123/udp/{key}\n'

    def row(self,name,key):
        row = {'endpoint':key,'validation':'media','protocol':'udp','programs':[{'id':'1','name':name}],
                'streams':[{'type':'video','codec':'h264'}], 'successes':2,'first_success':100000,'last_success':100100}
        row.update(success_checks=[100000,100100],success_fingerprint=media_fingerprint(key,row))
        return row

    def test_add_in_existing_order(self):
        base='#EXTM3U x-tvg-url="kept"\n'+self.entry('CCTV-1','239.10.1.1:8000')+self.entry('CCTV-3','239.10.1.3:8000')+self.entry('山东卫视','239.10.1.4:8000')+self.entry('BBC','239.10.1.5:8000')
        rows=[self.row('CCTV-2','239.10.1.2:8000'),self.row('新地方台','239.10.1.6:8000')]
        files,changes,pending=plan(base,base,rows,now=100101)
        self.assertEqual([x['name'] for x in parse(files['new.m3u'])[1]],['CCTV-1','CCTV-2','CCTV-3','山东卫视','新地方台','BBC'])
        self.assertIn('x-tvg-url="kept"',files['new.m3u'])
        self.assertIn('group-title="原分组"',files['new.m3u'])
        self.assertEqual(len(changes),2); self.assertFalse(pending)
        self.assertFalse(plan(files['new.m3u'],files['new-logo.m3u'],rows,now=100101)[0])

    def test_no_delete_and_no_single_probe(self):
        base='#EXTM3U\n'+self.entry('CCTV-1','239.10.1.1:8000')
        row=self.row('CCTV-2','239.10.1.2:8000'); row['successes']=1
        self.assertFalse(plan(base,base,[row],now=100101)[0])
        row=self.row('CCTV-1','239.10.1.2:8000')
        self.assertFalse(plan(base,base,[row],now=100101)[0])
        self.assertEqual(len(plan(base,base,[row],now=100101)[2]),1)

    def test_replace_only_sustained_failure_keeps_metadata(self):
        base='#EXTM3U\n'+self.entry('CCTV-1','239.10.1.1:8000')
        logo='#EXTM3U\n'+self.entry('CCTV-1','239.10.1.1:8000',True)
        rows=[self.row('CCTV1','239.10.1.2:8000'),{'endpoint':'239.10.1.1:8000','validation':'no_response','failures':3,'first_failure':1000,'last_failure':100100}]
        now=dt.datetime(2026,10,27,10,30,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        rows[0].update(success_checks=[now-120,now-1],last_success=now-1,first_success=now-120)
        rows[1].update(daytime_failure_checks=[now-90000,now-86400,now],last_failure=now)
        files,changes,_=plan(base,logo,rows,now=now)
        self.assertEqual(changes[0]['kind'],'replace')
        self.assertIn('tvg-id="kept"',files['new.m3u'])
        self.assertIn('tvg-logo="http://router:8080/iptv/logos/cctv.png"',files['new-logo.m3u'])
        self.assertEqual(len(parse(files['new.m3u'])[1]),1)

    def test_night_or_legacy_failures_cannot_replace(self):
        base='#EXTM3U\n'+self.entry('CCTV-1','239.10.1.1:8000')
        now=dt.datetime(2026,10,28,10,30,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        row=self.row('CCTV1','239.10.1.2:8000')
        row.update(success_checks=[now-120,now-1],last_success=now-1)
        old={'endpoint':'239.10.1.1:8000','validation':'no_response','failures':99,'first_failure':now-3*86400,'last_failure':now}
        for checks in ([],[now-7*3600-i*86400 for i in range(3)],[now-86400,now-86399,now]):
            old['daytime_failure_checks']=checks
            self.assertFalse(plan(base,base,[row,old],now=now)[0])

    def test_replacement_window_collection_and_merge_boundaries(self):
        base='#EXTM3U\n'+self.entry('CCTV-1','239.10.1.1:8000')
        logo='#EXTM3U\n'+self.entry('CCTV-1','239.10.1.1:8000',True)
        for hour,minute,allowed in [(8,59,False),(9,0,True),(17,59,True),
                                    (18,30,True),(19,0,True),(21,59,True),(22,0,False)]:
            with self.subTest(hour=hour,minute=minute), tempfile.TemporaryDirectory() as temp:
                now=dt.datetime(2026,10,28,hour,minute,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
                worker=Worker(Path(temp)/'state.json','192.0.2.1')
                key='239.10.1.1:8000';stamps=[now-2*86400,now-86400,now]
                for stamp in stamps:
                    with patch('server.time.time',return_value=stamp):
                        worker.observe(key,{'validation':'no_response'})
                old=worker.state['observations'][key]
                self.assertEqual(old['daytime_failure_checks'],stamps if allowed else [])
                self.assertFalse(old.get('archive_failure_checks'))
                row=self.row('CCTV1','239.10.1.2:8000')
                row.update(success_checks=[now-120,now-1],last_success=now-1)
                files,changes,pending=plan(base,logo,[row,dict(old,endpoint=key)],now=now)
                self.assertEqual(bool(files),allowed)
                self.assertEqual([c['kind'] for c in changes],['replace'] if allowed else [])
                self.assertEqual(bool(pending),not allowed)
                if allowed:
                    self.assertIn('tvg-id="kept"',files['new.m3u'])
                    self.assertIn('group-title="原分组"',files['new.m3u'])
                    self.assertIn('tvg-logo="http://router:8080/iptv/logos/cctv.png"',files['new-logo.m3u'])
                    self.assertEqual(len(parse(files['new.m3u'])[1]),1)

    def test_evening_replacement_preserves_spacing_age_and_identity_guards(self):
        now=dt.datetime(2026,10,28,19,0,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        key='239.10.1.1:8000';base='#EXTM3U\n'+self.entry('CCTV-1',key)
        row=self.row('CCTV1','239.10.1.2:8000')
        row.update(success_checks=[now-120,now-1],last_success=now-1)
        good=[now-86400,now-86400+3600,now]
        old={'endpoint':key,'validation':'no_response','last_failure':now,'daytime_failure_checks':good}
        self.assertEqual(plan(base,base,[row,old],now=now)[1][0]['kind'],'replace')
        for checks in ([],[now-86400,now-86399,now],[now-7200,now-3600,now],
                       [now-10*86400,now-9*86400,now-8*86400]):
            with self.subTest(checks=checks):
                self.assertFalse(plan(base,base,[row,dict(old,daytime_failure_checks=checks)],now=now)[0])
        for change in ({'success_checks':[now-1]}, {'protocol':'rtp'}, {'last_check':'no_response'}):
            with self.subTest(change=change):
                self.assertFalse(plan(base,base,[dict(row,**change),old],now=now)[0])
        duplicate=base+self.entry('CCTV1','239.10.1.3:8000')
        self.assertFalse(plan(duplicate,duplicate,[row,old],now=now)[0])
        self.assertFalse(plan(base,base,[row,dict(old,validation='media')],now=now)[0])

    def test_evening_failure_streak_resets_after_success_or_uncertainty(self):
        now=dt.datetime(2026,10,28,19,0,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        for result in ('media','inconclusive','no_media'):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as temp:
                worker=Worker(Path(temp)/'state.json','192.0.2.1');key='239.10.1.1:8000'
                for stamp in (now-86400,now-82800,now):
                    with patch('server.time.time',return_value=stamp):worker.observe(key,{'validation':'no_response'})
                self.assertEqual(len(worker.state['observations'][key]['daytime_failure_checks']),3)
                with patch('server.time.time',return_value=now+1):worker.observe(key,{'validation':result})
                self.assertEqual(worker.state['observations'][key]['daytime_failure_checks'],[])

    def test_stale_and_changed_success_evidence_refuses(self):
        row=self.row('新频道','239.10.1.2:8000')
        for change in ({'success_checks':[]},{'success_checks':[100000,100001]},
                       {'success_checks':[100000,100100+8*86400]}, {'protocol':'rtp'},
                       {'streams':[{'type':'video','codec':'hevc'}]}):
            self.assertIsNone(eligible(dict(row,**change),100101))
        old=dict(row);old.pop('success_fingerprint')
        self.assertIsNone(eligible(old,100101))

    def test_unidentified_stale_injection_and_multiservice(self):
        for name in ['Service01','test','bad\nname','bad"name']:
            self.assertIsNone(eligible(self.row(name,'239.10.1.2:8000'),100101))
        row=self.row('新频道','239.10.1.2:8000')
        self.assertIsNone(eligible(row,100100+8*86400))
        row['programs'].append({'name':'另一频道'})
        self.assertIsNone(eligible(row,100101))
        self.assertNotEqual(identity('CCTV-1 4K'),identity('CCTV-1'))

    def test_mismatched_pair_refuses(self):
        with self.assertRaises(ValueError):
            plan('#EXTM3U\n'+self.entry('one','239.10.1.1:8000'),'#EXTM3U\n'+self.entry('two','239.10.1.1:8000'),[])

    def test_apply_backup_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            www=Path(d)/'www'; www.mkdir(); backup=Path(d)/'backups'
            base='#EXTM3U\n'+self.entry('CCTV-1','239.10.1.1:8000')
            (www/'new.m3u').write_text(base);(www/'new-logo.m3u').write_text(base)
            with patch('playlist_update.time.time',return_value=100101):
                result=apply(www,backup,[self.row('CCTV-2','239.10.1.2:8000')])
            self.assertEqual(result['state'],'applied')
            self.assertEqual((Path(result['backup'])/'new.m3u').read_text(),base)
            self.assertEqual(len(parse((www/'new.m3u').read_text())[1]),2)
            # A failure halfway through restores both original files.
            import playlist_update
            real=playlist_update.atomic_bytes
            def fail_second(path,data,mode=0o644):
                if path==www/'new-logo.m3u': raise OSError('simulated write failure')
                return real(path,data,mode)
            before=(www/'new.m3u').read_bytes()
            with patch('playlist_update.time.time',return_value=100101), patch('playlist_update.atomic_bytes',side_effect=fail_second), self.assertRaises(OSError):
                apply(www,backup,[self.row('CCTV-3','239.10.1.3:8000')])
            self.assertEqual((www/'new.m3u').read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
