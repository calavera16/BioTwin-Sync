import json
from http.client import HTTPConnection
import unittest
from biotwin.presentation import PresentationHost
from biotwin.desktop_assets import load_examples
from tests.test_presentation import AssetsFixture

class SourceReplayTests(unittest.TestCase):
    def test_authenticated_bounded_sources_and_isolated_proof(self):
        with AssetsFixture() as directory, PresentationHost(directory=directory) as host:
            def get(path, auth=True, origin=None):
                connection=HTTPConnection('127.0.0.1',host.server.server_address[1],timeout=5)
                headers={'X-BioTwin-Token':host.token} if auth else {}
                if origin: headers['Origin']=origin
                connection.request('GET',path,headers=headers)
                response=connection.getresponse(); result=(response.status,json.loads(response.read()));connection.close();return result
            initial=host.controller.state('crew','Crew 001')
            host.controller.action({'role':'crew','crew':'Crew 001','action':'start_local','expected_revision':0, 'expected_session_id':initial['session_id'],'expected_digest':initial['projection']['digest'],'payload':{}})
            before=host.controller.state('crew','Crew 001')
            doctor_before=host.controller.state('doctor','Crew 001')
            revision_before=host.controller.revision
            self.assertEqual(get('/api/source-replay?example=crew001_ecg.json',False)[0],403)
            self.assertEqual(get('/api/mission-proof',False)[0],403)
            self.assertEqual(get('/api/source-replay?example=crew001_ecg.json',origin='http://example.com')[0],403)
            for query in ('example=../../x','example=crew001_ecg.json&start=0','example=crew001_ecg.json&example=crew001_ecg.json','example=crew001_ecg.json&'):
                self.assertEqual(get('/api/source-replay?'+query)[0],400)
            for example in load_examples():
                status,data=get('/api/source-replay?example='+example['file'])
                self.assertEqual(status,200)
                self.assertLessEqual(len(data['points']),7200)
                self.assertEqual(data['raw_sample_count'],len(example['times']))
                original=dict(zip(example['times'],example['values']))
                self.assertTrue(all(p['t'] in original and p['value']==original[p['t']] for p in data['points']))
                self.assertEqual(data['source_key'],example['source_key'])
                self.assertEqual(data['source_end'],example['times'][-1])
                finite=[v for v in example['values'] if v is not None]
                self.assertEqual(data['amplitude_min'],min(finite))
                self.assertEqual(data['amplitude_max'],max(finite))
            status,proof=get('/api/mission-proof')
            self.assertEqual(status,200)
            self.assertEqual(proof['phases'][-1]['crew']['checklist']['branch'],'B')
            self.assertEqual(get('/api/mission-proof?extra=1')[0],400)
            self.assertEqual(before,host.controller.state('crew','Crew 001'))
            self.assertEqual(doctor_before,host.controller.state('doctor','Crew 001'))
            self.assertEqual(host.controller.revision,revision_before)
