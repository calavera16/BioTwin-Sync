"""Presentation regression: real projections, role boundaries and usable Tk workflow."""
import copy
import tkinter as tk
import unittest
from unittest.mock import patch

from biotwin.desktop import DesktopApp
from biotwin.desktop_controller import DesktopController
from biotwin.desktop_presentation import (GUIDED_END_SECONDS, evidence_summary, reference_label,
    time_label, received_time, evidence_age, single_observation_label)
from biotwin.mission import mission_projection


class EvidencePresentationTests(unittest.TestCase):
    def test_utc_labels_normalize_offsets_and_do_not_invent_timezone(self):
        self.assertEqual(time_label('2026-11-14T18:30:00+09:00'),'09:30 UTC')
        self.assertEqual(time_label('2026-11-14T09:30:00Z'),'09:30 UTC')
        self.assertIn('timezone unverified',time_label('2026-11-14T09:30:00'))
        self.assertNotIn('UTC',time_label('not a date'))
        self.assertEqual(evidence_age(None,'2026-11-14T09:30:00Z'),'Age unavailable')
        self.assertEqual(evidence_age('2026-11-14T09:31:00Z','2026-11-14T09:30:00Z'),'Age unavailable')

    def test_received_summary_is_scoped_and_preserves_missing_reference(self):
        c=DesktopController(); c.seek(3); model=c.model(); projection=mission_projection(model)
        before=copy.deepcopy(model)
        crew=evidence_summary(model['crew'],projection['crew'])
        doctor=evidence_summary(model['doctor'],projection['doctor'])
        self.assertIn('585 ms',crew); self.assertIn('+12.78 SD',crew); self.assertIn('n=7',crew)
        self.assertIn('420 ms',doctor); self.assertNotIn('585',doctor); self.assertNotIn('09:30',doctor)
        self.assertIn('Observed: 09:00 UTC',doctor); self.assertIn('Received: 09:20 UTC',doctor)
        self.assertEqual(received_time(model['doctor']),'2026-11-14T09:20:00+00:00')
        self.assertEqual(model,before)
        c.seek(0); model=c.model(); projection=mission_projection(model)
        self.assertIn('No snapshot available',evidence_summary(model['doctor'],projection['doctor']))
        c.start_local(); model=c.model(); projection=mission_projection(model)
        local=evidence_summary(model['crew'],projection['crew'])
        self.assertNotIn('420 ms',local); self.assertNotIn('585 ms',local)
        self.assertIn('unavailable',reference_label(projection['crew']['metric']).lower())
        self.assertNotIn('n=7',reference_label(projection['crew']['metric']))

    def test_single_observation_words_follow_actual_flag_and_preserve_raw_reason(self):
        c=DesktopController()
        for stop,expected in ((0,'No single-observation deviation flagged.'),(2,'Single-observation deviation flagged for review.')):
            c.seek(stop);model=c.model();projection=mission_projection(model)
            before=copy.deepcopy((model,projection))
            self.assertEqual(projection['crew']['metric']['change']['persistence'],1)
            self.assertIn(expected,evidence_summary(model['crew'],projection['crew']))
            self.assertNotIn('persistent change flagged',evidence_summary(model['crew'],projection['crew']))
            self.assertEqual((model,projection),before)
        self.assertEqual(projection['crew']['metric']['change']['reason'],'persistent change flagged')
        c.start_local();metric=mission_projection(c.model())['crew']['metric']
        self.assertEqual(single_observation_label(metric),'Single-observation comparison unavailable: '+metric['change']['reason']+'.')
        # Wording follows the supplied projection flag, never a second z-score calculation.
        metric=copy.deepcopy(projection['crew']['metric']);metric['change']['flagged']=False
        self.assertEqual(single_observation_label(metric),'No single-observation deviation flagged.')

    def test_other_local_measurements_are_readable_only_after_delivery(self):
        from datetime import datetime,timezone
        c=DesktopController();c.start_local()
        row=dict(subject_id=c.subject,source_id='Entered cuff',metric='heart_rate',value=72,unit='bpm',
                 observed_at=datetime.now(timezone.utc).isoformat(),method='Manual instrument reading',quality='usable')
        c.capture_manual(row);c.send_consultation();model=c.model();projection=mission_projection(model)
        self.assertIn('72 bpm',evidence_summary(model['crew'],projection['crew']))
        self.assertNotIn('72 bpm',evidence_summary(model['doctor'],projection['doctor']))
        c.advance();model=c.model();projection=mission_projection(model)
        doctor=evidence_summary(model['doctor'],projection['doctor'])
        self.assertIn('heart_rate: 72 bpm',doctor);self.assertIn('Entered cuff',doctor)
        self.assertIn('quality: usable',doctor);self.assertIn('Reference / SD unavailable',doctor)
        c.capture_manual({**row,'source_id':'New onboard cuff','value':83})
        model=c.model();projection=mission_projection(model)
        self.assertNotIn('83 bpm',evidence_summary(model['doctor'],projection['doctor']))
        self.assertNotIn('New onboard cuff',evidence_summary(model['doctor'],projection['doctor']))

    def test_receipt_time_handles_delivered_full_delta_and_resync_without_rejected_packet(self):
        from biotwin.simulation import synthetic_fixture
        from biotwin.protocol import make_snapshot
        from biotwin.transport import make_delta,full_packet
        from biotwin.viewmodel import role_view
        old=synthetic_fixture()['events'][0]['snapshot'];payload=old['payload']
        at=lambda minute:f'2026-11-14T09:{minute:02}:00+00:00'
        middle=make_snapshot(payload['subject_id'],payload['observations'],at(1))
        local=[dict(event_id='local-'+str(i),kind='local',actor='crew',at=snap['payload']['created_at'],snapshot=snap) for i,snap in enumerate((old,middle))]
        def packet(identity,sent,arrived,content):
            return dict(event_id=identity,kind='packet',packet_id=identity,sender='crew',recipient='doctor',sent_at=at(sent),arrives_at=at(arrived),packet=content)
        for kind,envelope in (('delta',make_delta(old,middle)),('full',full_packet(middle,old['digest']))):
            with self.subTest(kind=kind):
                events=local+[packet('initial',0,3,{'type':'snapshot','snapshot':old}),packet('update',4,5,{'type':'envelope','envelope':envelope}),packet('rejected',5,6,{'type':'resync','envelope':full_packet(middle)})]
                self.assertEqual(received_time(role_view(events,at(4),'doctor')),at(3))
                role=role_view(events,at(6),'doctor')
                self.assertEqual(role['current_snapshot']['digest'],middle['digest'])
                self.assertNotEqual(role['inbox'][-1]['status'],'received')
                self.assertEqual(received_time(role),at(5))
        events=local+[packet('missing',2,3,{'type':'envelope','envelope':make_delta(old,middle)}),packet('resync',4,5,{'type':'resync','envelope':full_packet(middle)})]
        self.assertIsNone(received_time(role_view(events,at(4),'doctor')))
        self.assertEqual(received_time(role_view(events,at(5),'doctor')),at(5))
        c=DesktopController();c.seek(7)
        self.assertEqual(received_time(c.model()['doctor']),'2026-11-14T10:05:00+00:00')


class PresentationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.withdraw(); self.errors=[]
        self.root.report_callback_exception=lambda *args,errors=self.errors:errors.append(args)
        with patch.object(DesktopApp,'discover_references'):
            self.app=DesktopApp(self.root,reduced_motion=True)
        self.root.attributes('-alpha',0.0); self.root.geometry('1050x720+20000+20000')
        self.root.deiconify(); self.settle()

    def settle(self):
        for _ in range(4): self.root.update()

    def tearDown(self):
        self.app.close(); self.assertFalse(self.errors)

    def test_intro_uses_same_actual_reference_as_condition_without_mutation(self):
        a=self.app; a.jump(2); before=copy.deepcopy(a.controller.document())
        facts=a.guided_facts()
        self.assertEqual(facts['metric'],a.projection['crew']['metric'])
        self.assertEqual(facts['reference'],reference_label(a.projection['crew']['metric']))
        tour=a.show_introduction(); tour.window.attributes('-alpha',0); tour.index=1; tour.render()
        self.assertIn('+12.78 SD',tour.explanation.cget('text')); self.assertIn('n=7',tour.explanation.cget('text'))
        self.assertNotIn('+2.40',tour.explanation.cget('text'))
        self.assertIn('SYNTHETIC EXAMPLE',tour.progress.cget('text'))
        canvas_text=' '.join(tour.canvas.itemcget(i,'text') for i in tour.canvas.find_all() if tour.canvas.type(i)=='text')
        self.assertIn('585 ms',canvas_text);self.assertNotIn('+12.78',canvas_text)
        self.assertEqual(a.controller.document(),before); tour.close()

    def test_summary_uses_single_observation_wording_and_technical_json_keeps_reason(self):
        a=self.app;a.jump(2);before=copy.deepcopy(a.controller.document())
        window=a.show_role('crew');window.attributes('-alpha',0)
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        boxes=[widget for widget in descendants(window) if isinstance(widget,tk.Text)]
        self.assertEqual(len(boxes),2)
        summary,technical=[box.get('1.0','end') for box in boxes]
        self.assertIn('Single-observation deviation flagged for review.',summary)
        self.assertNotIn('persistent change flagged',summary)
        self.assertIn('"reason": "persistent change flagged"',technical)
        import json
        raw=json.loads(technical)
        self.assertEqual(raw['role_evidence'],a.model['crew']);self.assertEqual(raw['role_projection'],a.projection['crew'])
        self.assertNotIn('doctor',raw)
        self.assertEqual(a.controller.document(),before);window.destroy()

    def test_guided_playback_stops_and_holds_at_advertised_endpoint(self):
        a=self.app; a.open_monitor('Crew 001'); a.controller.seek_seconds(2399)
        a.consult_clock.seek(2399,now=10); a.consult_clock.running=True; a.consult_clock.anchor=10; a.playing=True
        with patch('biotwin.desktop_animation.time.monotonic',return_value=11): a.play_tick()
        self.assertEqual(a.consultation_seconds(),GUIDED_END_SECONDS)
        self.assertFalse(a.playing); self.assertIsNone(a.play_job)
        self.assertEqual(a.projection['crew']['checklist']['completion_count'],0)
        before=copy.deepcopy(a.controller.document()); a.launch_guided_demo()
        self.assertEqual(a.controller.document(),before); self.assertFalse(a.playing)
        a.step(); self.assertGreater(a.consultation_seconds(),GUIDED_END_SECONDS)
        self.assertEqual(a.projection['crew']['checklist']['completion_count'],3)
        a.controller.start_local(); before=copy.deepcopy(a.controller.document()); a.launch_guided_demo()
        self.assertEqual(a.controller.document(),before)

    def test_single_visible_play_control_and_long_cues_fit_with_timeline(self):
        a=self.app; a.open_monitor('Crew 001');a.toggle_demo();self.settle()
        self.assertFalse(a.quick_demo_button.winfo_ismapped());self.assertTrue(a.play_button.winfo_ismapped())
        c=a.controller;c.start_local();a.refresh()
        for mode in ('unknown','urgent'):
            if mode=='urgent':
                path=c.assistant_catalog()['protocols'][0]
                urgent=next(q['id'] for q in path['questions'] if q['kind']=='urgent')
                c.assess_assistant(path['id'],{urgent:'yes'},expected_context=c.assistant_context());a.refresh()
            self.settle();canvas=a.condition_cue;scope=canvas.bbox('condition-scope')
            self.assertLessEqual(canvas.winfo_rooty()+scope[3]+8,a.action_panel.winfo_rooty())
            self.assertLessEqual(scope[2],canvas.winfo_width())
            self.assertGreater(a.assistant_button.winfo_height(),20)
            if a.checklist_button.winfo_ismapped():
                self.assertLessEqual(a.checklist_button.winfo_rooty()+a.checklist_button.winfo_height(),a.action_panel.winfo_rooty()+a.action_panel.winfo_height())
        a.toggle_demo();self.settle();self.assertTrue(a.quick_demo_button.winfo_ismapped())
        self.assertFalse(a.play_button.winfo_ismapped())

    def test_full_checklist_keyboard_and_footer_fit_minimum_window(self):
        a=self.app; a.open_monitor('Crew 001'); a.jump(3); self.settle()
        a.checklist_window.attributes('-alpha',0); a.assistant_button.invoke()
        a.checklist_window.geometry('640x530+20000+20000'); self.settle()
        self.assertEqual(a.checklist_window.state(),'normal')
        self.assertTrue(a.steps_scrollbar.winfo_ismapped()); self.assertGreater(a.steps_canvas.winfo_height(),150)
        for widget in (a.note,a.complete_button,a.capture_button,a.followup_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreater(widget.winfo_height(),20)
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),a.checklist_window.winfo_rooty()+530)
        self.assertEqual(len(a.step_widgets),3)
        for index,button in enumerate(a.step_widgets):
            self.assertIn(a.projection['crew']['checklist']['steps'][index],button.cget('text'))
            button.focus_force(); self.settle()
            self.assertEqual(str(self.root.focus_get()),str(button))
            self.assertLess(button.winfo_y(),a.steps_frame.winfo_height())
        self.assertTrue(a.complete_button.instate(['disabled']))
        a.checklist_window.event_generate('<Escape>'); self.settle()
        self.assertEqual(a.checklist_window.state(),'withdrawn'); self.assertEqual(a.screen,'monitor')

    def test_explicit_recording_and_followup_in_workspace_keep_existing_semantics(self):
        a=self.app; c=a.controller; c.reset(interactive=True)
        c.capture(585);c.send_consultation();c.advance()
        c.author_reply('le',450,'Repeat supported measurement','Confirm device','Repeat measurement\nRecord context')
        c.advance();a.open_monitor('Crew 001');a.refresh()
        a.checklist_window.attributes('-alpha',0);a.open_checklist();self.settle()
        a.step_widgets[0].invoke(); a.note.insert(0,'Confirmed same device and task.');a.complete_button.invoke()
        self.assertEqual(a.projection['crew']['checklist']['completion_count'],1)
        a.followup_button.invoke();self.assertFalse(a.projection['doctor']['followups'])
        c.advance();a.refresh();self.assertEqual(len(a.projection['doctor']['followups']),1)
        a.open_monitor('Crew 002');self.assertEqual(a.checklist_window.state(),'withdrawn');self.assertEqual(a.note.get(),'')

    def test_long_step_remains_wrapped_after_completion_rebuild_at_minimum_size(self):
        a=self.app;c=a.controller;c.reset(interactive=True)
        c.capture(585);c.send_consultation();c.advance()
        long='Confirm source and device, record measurement conditions and distractions. '*6
        c.author_reply('le',450,'Repeat supported measurement',long,long+'\nRecord follow-up context')
        c.advance();a.open_monitor('Crew 001');a.checklist_window.attributes('-alpha',0)
        a.open_checklist();a.checklist_window.geometry('640x530+20000+20000');self.settle()
        width=a.steps_canvas.winfo_width();before=a.step_widgets[0].winfo_reqheight()
        a.select_step(0);a.note.insert(0,'Confirmed conditions explicitly.');a.complete_button.invoke();self.settle()
        self.assertEqual(a.projection['crew']['checklist']['completion_count'],1)
        self.assertEqual(a.steps_canvas.winfo_width(),width)
        button=a.step_widgets[0]
        self.assertLessEqual(int(button.cget('wraplength')),width-48)
        self.assertLessEqual(button.winfo_reqwidth(),width)
        self.assertGreaterEqual(button.winfo_reqheight(),before)
        self.assertIn(long.strip(),button.cget('text'))
        self.assertTrue(a.steps_scrollbar.winfo_ismapped())
        self.assertLessEqual(a.complete_button.winfo_rooty()+a.complete_button.winfo_height(),a.checklist_window.winfo_rooty()+530)

    def test_stale_workspace_cannot_apply_notes_to_changed_session_or_evidence(self):
        a=self.app;c=a.controller;c.reset(interactive=True)
        c.capture(585);c.send_consultation();c.advance()
        c.author_reply('le',450,'Repeat supported measurement','Confirm device','Repeat measurement\nRecord context')
        c.advance();a.open_monitor('Crew 001');a.checklist_window.attributes('-alpha',0)
        a.select_step(0);a.note.insert(0,'Old context note')
        # Deliberately change the controller without refreshing the presentation.
        c.capture(560);before=copy.deepcopy(c.document());a.complete()
        self.assertEqual(c.document(),before);self.assertEqual(a.note.get(),'')
        self.assertEqual(a.checklist_window.state(),'withdrawn')
        a.refresh();a.open_checklist();a.select_step(0);a.note.insert(0,'Reset context note')
        c.reset(interactive=True);before=copy.deepcopy(c.document());a.complete()
        self.assertEqual(c.document(),before);self.assertEqual(a.note.get(),'')
        a.refresh();self.assertEqual(a.checklist_window.state(),'withdrawn')

    def test_roster_metric_name_and_value_fit_without_overlap_at_minimum_width(self):
        a=self.app
        for size in ('1050x720','1280x800'):
            self.root.geometry(size+'+20000+20000')
            for disclosed in (False,True):
                if a.demo_open!=disclosed:a.toggle_demo()
                a.show_home();self.settle()
                for card in a.crew_cards.values():
                    canvas=card['chart']
                    source=canvas.bbox('source-label');name=canvas.bbox('roster-metric-name');value=canvas.bbox('roster-measurement')
                    self.assertEqual(canvas.itemcget(canvas.find_withtag('roster-metric-name')[0],'text'),'Reaction time')
                    self.assertLess(source[3],name[1]);self.assertLess(name[3],value[1])
                    for item in canvas.find_all():
                        if canvas.type(item)=='text':
                            box=canvas.bbox(item)
                            self.assertGreaterEqual(box[0],0);self.assertLessEqual(box[2],canvas.winfo_width())
                            self.assertLessEqual(box[3],canvas.winfo_height())

    def test_home_identity_and_earth_columns_stay_stable_without_source_overlap(self):
        a=self.app
        for card in a.crew_cards.values():
            canvas=card['chart']; identity=canvas.bbox('crew-id'); source=canvas.bbox('source-label')
            self.assertLess(identity[3],source[1])
        a.open_monitor('Crew 001');self.settle(); states=[]
        for stop in (0,2,3):
            a.jump(stop);self.settle()
            canvas=a.communication
            texts={canvas.itemcget(i,'text'):canvas.coords(i) for i in canvas.find_all() if canvas.type(i)=='text'}
            states.append((texts['Crew now'],texts['Earth last received']))
            for item in canvas.find_all():
                if canvas.type(item)=='text':
                    box=canvas.bbox(item)
                    self.assertGreaterEqual(box[0],0);self.assertLessEqual(box[2],canvas.winfo_width())
                    self.assertLessEqual(box[3],canvas.winfo_height())
            # Value strings must not wrap over observed/received timestamps in either stable column.
            for x in (12,12+canvas.winfo_width()/2):
                column=sorted((canvas.coords(i)[1],canvas.bbox(i)) for i in canvas.find_all() if canvas.type(i)=='text' and abs(canvas.coords(i)[0]-x)<1)
                for (_,upper),(_,lower) in zip(column,column[1:]): self.assertLessEqual(upper[3],lower[1])
            self.assertIn('585 ms' if stop else '420 ms',texts)
            if stop:self.assertIn('420 ms',texts);self.assertIn('Received 09:20',texts)
            else:self.assertIn('Unavailable',texts)
        self.assertEqual(states[0],states[1]);self.assertEqual(states[1],states[2])
        a.select_region('heart');self.settle()
        self.assertTrue(a.nasa_banner.winfo_ismapped())
        self.assertIn('separate from this crew',a.nasa_banner.cget('text'))
        self.assertIn('units unverified',a.nasa_banner.cget('text'))


if __name__=='__main__': unittest.main()
