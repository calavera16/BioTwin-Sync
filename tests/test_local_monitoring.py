import copy
import hashlib
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from biotwin.desktop_controller import DesktopController,CREWS
from biotwin.local_monitoring import export_import_examples,import_observations,import_signal,safe_path
from biotwin.mission import mission_projection
from biotwin.reaction_check import ReactionCheck
from biotwin import local_monitoring as local
from biotwin.protocol import canonical_bytes


class LocalMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.c=DesktopController(); self.c.start_local()
        self.at=datetime.now(timezone.utc).isoformat()

    def row(self,**overrides):
        return {'subject_id':self.c.subject,'metric':'heart_rate','value':72,'unit':'bpm',
            'source_id':'manual-device','method':'user-entered instrument reading','quality':'usable',
            'observed_at':self.at,**overrides}

    def signal(self,**overrides):
        return {'subject_id':self.c.subject,'channel':'ECG','samples':[math.sin(i/5) for i in range(100)],
            'sample_rate_hz':100,'unit':'mV','source_id':'device-file','method':'user exported ECG',
            'quality':'review','observed_at':self.at,**overrides}

    def test_empty_local_session_does_not_borrow_synthetic_evidence(self):
        self.assertEqual(self.c.model()['crew']['current_snapshot']['payload']['observations'],[])
        self.assertFalse(self.c.prerequisites()['send'][0])
        self.assertEqual(mission_projection(self.c.model())['crew']['status'],'Measurement needed')
        with self.assertRaises(ValueError): self.c.capture(450)
        self.c.select(CREWS[1]); self.assertTrue(self.c.model()['crew']['current_snapshot']['payload']['observations'])

    def test_manual_evidence_enters_actual_delayed_review_loop(self):
        self.c.capture_manual(self.row()); projected=mission_projection(self.c.model())
        self.assertEqual(projected['crew']['sensors']['HR']['value'],72)
        self.assertIsNone(projected['doctor']['sensors']['HR']['value'])
        self.c.send_consultation(); self.c.advance()
        self.assertEqual(mission_projection(self.c.model())['doctor']['sensors']['HR']['value'],72)
        self.c.author_reply('le',80,'Confirm measurement method.','Repeat same instrument','Record acquisition context',metric='heart_rate')
        self.c.advance(); self.assertEqual(self.c.model()['crew']['evaluation']['outcome'],'A')
        self.c.complete(0,'Repeated the instrument measurement.'); self.c.followup(); self.c.advance()
        self.assertEqual(len(self.c.model()['doctor']['followups']),1)

    def test_invalid_import_is_atomic_and_unknown_is_unavailable(self):
        self.c.capture_manual(self.row()); before=self.c.document()
        for changes in ({'unit':'Hz'},{'value':float('nan')},{'subject_id':'LOCAL:other'},
                        {'source_id':'SYNTHETIC:demo'},{'observed_at':'2026-01-01'},{'value':999},
                        {'quality':'healthy'},{'observed_at':'2099-01-01T00:00:00+00:00'}):
            with self.assertRaises(ValueError): self.c.capture_manual(self.row(**changes))
            self.assertEqual(before,self.c.document())
        self.c.capture_manual(self.row(value=None,quality='missing',observed_at=datetime.now(timezone.utc).isoformat()))
        self.assertIsNone(mission_projection(self.c.model())['crew']['sensors']['HR']['value'])

    def test_csv_json_observation_bounds_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'measurements.json'; path.write_text(json.dumps([self.row(),self.row(unit='Hz')]))
            before=self.c.document()
            with self.assertRaises(ValueError): self.c.import_observations(path)
            self.assertEqual(before,self.c.document())
            path.write_text(json.dumps([self.row(metric='sleep_duration',value=6.5,unit='h',method='self-reported diary')]))
            self.c.import_observations(path)
            sleep=mission_projection(self.c.model())['crew']['sensors']['sleep']
            self.assertEqual(sleep['value'],6.5); self.assertEqual(sleep['method'],'self-reported diary')
            csv=Path(directory)/'measurements.csv'; csv.write_text('subject_id,metric,value,unit,observed_at,source_id,method,quality\n'+
                f'{self.c.subject},spo2,97,%,{self.at},manual,entered device reading,review\n')
            self.c.import_observations(csv)
            self.assertIsNone(mission_projection(self.c.model())['crew']['sensors']['SpO2']['value'])
            path.write_text(json.dumps([self.row()]*501))
            with self.assertRaises(ValueError): self.c.import_observations(path)

    def test_signal_review_queue_gaps_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'signal.json'
            samples=self.signal()['samples']; samples[30]=None
            times=[i*.01+(0 if i<40 else .005) for i in range(100)]
            path.write_text(json.dumps(self.signal(samples=samples,sample_rate_hz=None,sample_times_seconds=times)))
            signal=self.c.add_signal(path)
            self.assertIsNone(signal['sample_rate_hz']); self.assertIsNone(signal['samples'][30])
            self.assertEqual(signal['sample_times_seconds'],times)
            self.c.mark_segment(signal['id'],20,40,'Gap requires review')
            request=self.c.request_repeat(signal['id'],'Repeat with acquisition context')
            self.c.resolve_repeat(request['id'],'New file requested from operator')
            self.c.send_consultation(); self.c.advance()
            rows=self.c.model()['doctor']['current_snapshot']['payload']['observations']
            latest=max(rows,key=lambda r:r['observed_at'])
            self.assertEqual(latest['signal_review']['repeat_requests'][0]['resolution'],'New file requested from operator')
            expected=self.c.document(); saved=self.c.save(directory); self.c.open(saved)
            self.assertEqual(expected,self.c.document())
            self.c.select(CREWS[1]); before=self.c.document()
            with self.assertRaises(ValueError): self.c.open(saved)
            self.assertEqual(before,self.c.document())

    def test_signal_import_rejects_fabricated_timing_and_other_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'signal.json'
            for overrides in ({'sample_rate_hz':None},{'sample_rate_hz':0}, {'samples':[True]*20},
                    {'source_id':'NASA:pilot'}, {'sample_times_seconds':[i*.02 for i in range(100)]},
                    {'sample_times_seconds':[0]*100}, {'samples':[0]*30001}):
                path.write_text(json.dumps(self.signal(**overrides)))
                before=self.c.document()
                with self.assertRaises(ValueError): self.c.add_signal(path)
                self.assertEqual(before,self.c.document())
        for name in ('.env','.ENV.secret','folder/.env.hidden/data.json'):
            with self.assertRaises(ValueError): safe_path(name)

    def test_reaction_actual_trials_and_method_identity(self):
        class Clock:
            now=0.0
            def __call__(self): return self.now
        class RNG:
            def uniform(self,a,b): return a
        clock=Clock(); task=ReactionCheck(clock=clock,rng=RNG()); task.start()
        for _ in range(5):
            clock.now=task.due; task.show_stimulus(); clock.now+=.3; task.respond()
        self.c.capture_reaction(task.summary())
        metric=mission_projection(self.c.model())['crew']['metric']
        self.assertEqual(metric['method'],'laptop-simple-reaction-v1')
        self.assertAlmostEqual(metric['value'],300); self.assertEqual(metric['reference']['status'],'unavailable')
        self.assertEqual(mission_projection(self.c.model())['crew']['status'],'Collecting baseline')
        bad=task.summary(); bad['configuration']['trials']=3
        with self.assertRaises(ValueError): self.c.capture_reaction(bad)
        with tempfile.TemporaryDirectory() as directory:
            expected=self.c.document(); self.c.open(self.c.save(directory)); self.assertEqual(expected,self.c.document())

    def test_examples_and_old_session_sources_remain_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=export_import_examples(directory,subject_id=self.c.subject)
            self.c.import_observations(next(p for p in paths if 'observation-template' in p))
            self.assertTrue(all(r['value'] is None and r['quality']=='missing' for r in self.c.model()['crew']['current_snapshot']['payload']['observations']))
            signal=self.c.add_signal(next(p for p in paths if 'synthetic-signal-example' in p))
            self.assertEqual(signal['provenance'],'synthetic'); self.assertEqual(signal['quality'],'review')
            old_source=signal['source_id']; self.c.start_local(); before=self.c.document()
            with self.assertRaises(ValueError): self.c.capture_manual(self.row(source_id=old_source))
            self.assertEqual(before,self.c.document())
            with self.assertRaises(ValueError): self.c.capture_manual(self.row(provenance='synthetic'))

    def test_local_integrity_failure_and_bad_segment_preserve_state(self):
        with tempfile.TemporaryDirectory() as directory:
            signal_path=Path(directory)/'signal.json'; signal_path.write_text(json.dumps(self.signal()))
            signal=self.c.add_signal(signal_path); before=self.c.document()
            for lo,hi in ((-1,10),(10,10),(0,101)):
                with self.assertRaises(ValueError): self.c.mark_segment(signal['id'],lo,hi,'Invalid')
                self.assertEqual(before,self.c.document())
            path=self.c.save(directory); doc=json.loads(Path(path).read_text(encoding='utf-8'))
            doc['payload']['local']['signals'][0]['samples'][0]=123
            Path(path).write_text(json.dumps(doc),encoding='utf-8')
            with self.assertRaises(ValueError): self.c.open(path)
            self.assertEqual(before,self.c.document())

    def test_repeated_capture_after_simulated_delivery_preserves_acquisition_time(self):
        self.c.capture_manual(self.row()); self.c.send_consultation(); self.c.advance()
        self.c.capture_manual(self.row(metric='temperature',value=36.8,unit='C'))
        first=self.c.current.at
        self.c.capture_manual(self.row(metric='sleep_duration',value=7,unit='h',method='self-reported diary'))
        self.assertGreater(self.c.current.at,first)
        self.assertTrue(all(r['observed_at']==self.at for r in self.c.model()['crew']['current_snapshot']['payload']['observations']))

    def test_clock_integrity_validation_and_legacy_actual_event_fallback(self):
        self.c.capture_manual(self.row()); self.c.send_consultation()
        doc=self.c.document(); before=copy.deepcopy(doc)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'clock.json'
            for value in ('2000-01-01T00:00:00+00:00','2026-11-15',None,True):
                bad=copy.deepcopy(doc); bad['payload']['replay_at']=value
                bad['sha256']=hashlib.sha256(canonical_bytes(bad['payload'])).hexdigest()
                path.write_text(json.dumps(bad),encoding='utf-8')
                with self.assertRaises(ValueError): self.c.open(path)
                self.assertEqual(self.c.document(),before)
            bad=copy.deepcopy(doc); bad['payload']['replay_at']='2099-01-01T00:00:00+00:00'
            path.write_text(json.dumps(bad),encoding='utf-8')
            with self.assertRaises(ValueError): self.c.open(path)
            legacy=local.local_document(doc['payload']['session'],doc['payload']['local'])
            path.write_text(json.dumps(legacy),encoding='utf-8')
            self.c.open(path)
            self.assertEqual(self.c.current.at,before['payload']['replay_at'])
            self.assertEqual(self.c.current.clock_basis,'inferred')
            self.assertIsNone(self.c.model()['doctor']['current_snapshot'])
            self.assertEqual(self.c.document()['version'],2)

    def test_native_packet_capacity_failure_is_atomic(self):
        self.c.capture_manual(self.row())
        before=self.c.document(); original_workflow=self.c.current.workflow
        with patch('biotwin.local_monitoring.MAX_BYTES',len(canonical_bytes(before))+1):
            with self.assertRaises(local.StorageCapacityError): self.c.send_consultation()
        self.assertEqual(self.c.document(),before)
        self.assertIs(self.c.current.workflow,original_workflow)
        with tempfile.TemporaryDirectory() as directory:
            self.c.open(self.c.save(directory))
            self.assertEqual(self.c.document(),before)


if __name__=='__main__': unittest.main()
