"""Navigation, disclosure and non-destructive introductory flow, with hidden Tk."""
import copy
import tkinter as tk
from tkinter import ttk
import time
import unittest
from unittest.mock import patch

from biotwin.desktop import DesktopApp
from biotwin.desktop_ux import Introduction, FiniteMotion
from biotwin.desktop_theme import INK, RAISED, SELECTED
from biotwin.workflow import Workflow


class DesktopUXTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.withdraw()
        self.errors=[]
        self.root.report_callback_exception=lambda *args,errors=self.errors:errors.append(args)
        with patch.object(DesktopApp,'discover_references'):
            self.app=DesktopApp(self.root,startup=False)
            self.root.update()

    def tearDown(self):
        self.app.close()
        self.assertFalse(self.errors)

    def hidden_tour(self,**kwargs):
        original=tk.Toplevel
        def hidden(*args,**options):
            window=original(*args,**options); window.withdraw(); return window
        with patch('biotwin.desktop_ux.tk.Toplevel',new=hidden):
            return self.app.show_introduction(**kwargs)

    def test_historical_followup_requires_confirmation_and_keeps_current_steps_empty(self):
        a=self.app; c=a.controller
        c.current.workflow=Workflow(c.current.workflow.events[:-1])
        c.current.mode='operator'; c.current.at='2026-11-14T09:44:00+00:00'
        a.open_checklist(); before=c.document()
        with patch('biotwin.desktop.messagebox.askyesno',return_value=False) as confirm:
            a.send_checklist_followup()
            self.assertIn('historical',confirm.call_args.args[0])
        self.assertEqual(c.document(),before)
        with patch('biotwin.desktop.messagebox.askyesno',return_value=True):
            a.send_checklist_followup()
        self.assertEqual(len(c.current.workflow.events),len(before['payload']['events'])+1)
        self.assertIn('no current step completed',a.status.get())
        self.assertEqual(a.projection['crew']['checklist']['current_completion_count'],0)

    def test_three_monitor_panels_one_focus_and_demo_disclosure(self):
        a=self.app; a.open_monitor('Crew 001')
        self.assertTrue(all(w.winfo_manager() for w in (a.condition_panel,a.action_panel,a.earth_panel)))
        self.assertFalse(a.timeline_area.winfo_manager())
        self.assertFalse(a.signals.winfo_manager())
        a.toggle_demo(); self.assertEqual(a.timeline_area.winfo_manager(),'pack')
        a.toggle_demo(); self.assertFalse(a.timeline_area.winfo_manager())
        a.select_region('heart'); a.select_channel('BVP')
        self.assertTrue(a.signals.winfo_manager())
        self.assertEqual(a.metric_value.cget('text'),'BVP')
        self.assertTrue(a.signal_panels['BVP']['controls'].winfo_manager())
        self.assertFalse(a.signal_panels['ECG']['controls'].winfo_manager())
        self.assertTrue(all(not p['chart'].winfo_manager() for p in a.signal_panels.values()))
        a.vital_selector.current(2); a.select_vital()
        self.assertEqual(a.metric_value.cget('text'),'Unavailable')
        self.assertFalse(a.signals.winfo_manager())
        a.select_region('head'); self.assertEqual(a.metric_value.cget('text'),'420 ms')
        self.assertEqual([a.earth_tabs.tab(tab,'text') for tab in a.earth_tabs.tabs()],['Messages','Review','References'])

    def test_default_cue_disclosures_and_home_return_preserve_evidence_and_binding(self):
        a=self.app; before=copy.deepcopy(a.controller.document())
        generation=a.controller._assistant_selection_generation
        a.open_monitor('Crew 001')
        self.assertFalse(a.measurements_open); self.assertFalse(a.evidence_frame.winfo_manager())
        self.assertTrue(a.condition_cue.winfo_manager()); self.assertFalse(a.earth_tabs.winfo_manager())
        self.assertFalse(a.signal_playing); self.assertIsNone(a.signal_job)
        self.assertEqual(set(a.figure_targets),{'head','heart','vital'})
        self.assertFalse(a.condition_cue.find_withtag('gradient'))
        self.assertFalse(a.crew_cards['Crew 001']['value'].winfo_manager())
        self.assertIn('Onboard assistant',[a.session_menu.entrycget(i,'label') for i in range(a.session_menu.index('end')+1)])
        for _ in range(3):
            a.toggle_measurements(); self.assertTrue(a.evidence_frame.winfo_manager())
            a.toggle_earth_review(); self.assertTrue(a.earth_tabs.winfo_manager())
            a.toggle_measurements(); a.toggle_earth_review()
        a.select_region('heart'); self.assertTrue(a.measurements_open)
        a.show_home(); a.open_monitor('Crew 001')
        self.assertFalse(a.measurements_open); self.assertTrue(a.condition_cue.winfo_manager())
        self.assertEqual(a.controller.document(),before)
        self.assertEqual(a.controller._assistant_selection_generation,generation)

    def test_idle_intake_expiry_and_uncertain_intake_route_to_assistant_with_received_steps(self):
        from datetime import datetime,timedelta,timezone
        a=self.app; c=a.controller; c.start_local()
        c.capture_manual(dict(subject_id=c.subject,source_id='QA-input',metric='heart_rate',value=72,
            unit='bpm',observed_at=datetime.now(timezone.utc).isoformat(),method='QA only',quality='usable'))
        c.send_consultation(); c.advance()
        c.author_reply('le',80,'QA repeat guidance','Record supported repeat','Review source',metric='heart_rate')
        c.advance(); path=c.assistant_catalog()['protocols'][0]
        c.assess_assistant(path['id'],{},expected_context=c.assistant_context())
        a.open_monitor('Crew 001'); self.assertTrue(a.step_widgets)
        self.assertEqual(a.crew_cue['target'],'assistant')
        self.assertEqual(a.assistant_button.cget('text'),'Open onboard assistant')
        with patch.object(a,'onboard_assistant') as open_assistant,patch.object(a,'select_step') as select:
            a.assistant_button.invoke(); open_assistant.assert_called_once(); select.assert_not_called()
        answers={q['id']:'no' for q in path['questions']}
        c.assess_assistant(path['id'],answers,expected_context=c.assistant_context()); a.refresh()
        document=c.document(); context=c.assistant_context()
        later=datetime.now(timezone.utc)+timedelta(minutes=31)
        a.cues_refreshed_at=0; self.root.after_cancel(a.poll_job)
        with patch('biotwin.desktop_controller.datetime',wraps=datetime) as controller_clock,patch('biotwin.desktop.datetime',wraps=datetime) as ui_clock:
            controller_clock.now.return_value=later; ui_clock.now.return_value=later
            a.poll_load()
        self.assertEqual(a.crew_cue['tone'],'amber'); self.assertIn('stale',a.crew_cue['detail'])
        self.assertEqual(c.document(),document); self.assertEqual(c.assistant_context(),context)

    def test_status_text_is_immediate_and_feedback_is_finite_and_cancelled(self):
        from unittest.mock import Mock
        a=self.app; a.open_monitor('Crew 001'); a.jump(2)
        self.assertEqual(a.crew_cue['tone'],'amber')
        self.assertTrue(any(a.condition_cue.itemcget(i,'text').endswith('An observation needs review') for i in a.condition_cue.find_all() if a.condition_cue.type(i)=='text'))
        job=a.feedback.job; self.assertIsNotNone(job)
        a.toggle_measurements(); self.assertNotIn(job,self.root.tk.call('after','info'))
        a.toggle_measurements(); a.toggle_motion(); a.jump(0)
        self.assertIsNone(a.feedback.job)
        draw=Mock(); animation=FiniteMotion(self.root)
        with patch('biotwin.desktop_ux.time.monotonic',side_effect=[10,10,10.25]):
            animation.start(draw); pending=animation.job
            self.root.after_cancel(pending); animation.tick()
        self.assertEqual(draw.call_args.args,(1.0,)); self.assertIsNone(animation.job)
        animation.close(); self.assertIsNone(animation.root)

    def test_tour_back_skip_finish_reopen_do_not_change_session_or_pause(self):
        a=self.app; a.jump(3); before=copy.deepcopy(a.controller.document())
        a.pause(); self.assertFalse(a.playing)
        tour=self.hidden_tour()
        self.assertEqual(tour.index,0); self.assertFalse(tour.window.grab_current())
        tour.forward(); tour.backward(); self.assertEqual(tour.index,0)
        tour.close(); self.assertIsNone(a.introduction)
        tour=self.hidden_tour()
        for _ in range(4): tour.forward()
        self.assertTrue(tour.closed); self.assertIsNone(a.introduction)
        self.assertEqual(a.controller.document(),before); self.assertFalse(a.playing)

    def test_animation_skip_reduced_motion_native_destroy_cleanup(self):
        a=self.app; before=a.controller.document()
        tour=self.hidden_tour(animate=True); job=tour.job
        self.assertIsNotNone(job)
        tour.close(); self.assertNotIn(job,self.root.tk.call('after','info'))
        a.toggle_motion(); tour=self.hidden_tour(animate=True)
        self.assertIsNone(tour.job); self.assertIn('1 OF 4',tour.progress.cget('text'))
        tour.window.destroy(); self.assertTrue(tour.closed); self.assertIsNone(a.introduction)
        a.open_monitor('Crew 001'); self.assertIsNone(a.signal_job)
        self.assertEqual(a.controller.document(),before)

    def test_close_cancels_pending_intro_and_active_intro_timers(self):
        self.app.close()
        self.root=tk.Tk(); self.root.withdraw()
        with patch.object(DesktopApp,'discover_references'):
            self.app=DesktopApp(self.root,startup=True)
        job=self.app.intro_job
        self.app.close()
        self.assertNotIn(job,self.root.tk.call('after','info'))
        self.assertIsNone(self.app.intro_job)

    def test_checklist_fixture_explanation_is_in_full_workspace_only(self):
        a=self.app; before=copy.deepcopy(a.controller.document())
        a.jump(3)
        self.assertIn('09:40',a.checklist_context.cget('text'))
        self.assertEqual(a.checklist_window.state(),'withdrawn')
        a.controller.start_local();a.refresh()
        self.assertNotIn('09:40',a.checklist_context.cget('text'))
        self.assertNotIn('Synthetic',a.checklist_context.cget('text'))
        self.assertFalse(a.step_widgets)

    def test_mapped_geometry_keeps_focus_graph_and_actions_at_supported_sizes(self):
        """Exercise the window manager layout invisibly, not requested widget sizes."""
        a=self.app
        a.toggle_motion(); a.open_monitor('Crew 001'); a.jump(3)
        a.select_region('heart'); a.toggle_demo()
        self.root.attributes('-alpha',0.0)
        self.root.deiconify()
        def settle():
            for _ in range(4):
                self.root.update(); time.sleep(.02)
        def inside(widget):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreater(widget.winfo_height(),20)
            self.assertLessEqual(widget.winfo_rootx()-self.root.winfo_rootx()+widget.winfo_width(),self.root.winfo_width())
            self.assertLessEqual(widget.winfo_rooty()-self.root.winfo_rooty()+widget.winfo_height(),self.root.winfo_height()-20)
        for size in ('1280x800','1366x768','1050x720'):
            with self.subTest(size=size):
                self.root.geometry(size+'+20000+20000'); settle()
                self.assertGreaterEqual(a.history.winfo_height(),170)
                self.assertGreaterEqual(a.history.winfo_width(),350)
                inside(a.assistant_button); inside(a.advance_button)
                # Every signed numeric amplitude label remains within the canvas.
                for item in a.history.find_all():
                    if a.history.type(item)=='text' and a.history.itemcget(item,'text').startswith('-'):
                        self.assertGreaterEqual(a.history.bbox(item)[0],0)
                a.set_measurements(False); settle()
                inside(a.condition_cue); inside(a.communication)
                self.assertGreaterEqual(a.condition_cue.winfo_height(),180)
                a.set_earth_review(True); a.earth_tabs.select(1); settle()
                inside(a.earth_tabs); inside(a.author_button)
                self.assertFalse(a.communication.winfo_ismapped())
                a.earth_tabs.select(2); settle(); inside(a.nlsp_button)
                a.earth_tabs.select(0); a.set_earth_review(False); a.set_measurements(True); settle()
        a.controller.reset(interactive=True); a.controller.capture(585)
        a.controller.send_consultation(); a.controller.advance()
        a.controller.author_reply('le',450,'Measure again','Confirm device','Repeat measurement\nRecord context')
        a.controller.advance(); a.refresh(); a.select_step(0); settle()
        self.assertGreaterEqual(a.history.winfo_height(),140)
        inside(a.advance_button)
        a.checklist_window.attributes('-alpha',0.0); a.open_checklist(); settle()
        for widget in (a.complete_button,a.followup_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),a.checklist_window.winfo_rooty()+a.checklist_window.winfo_height())
        style=ttk.Style(self.root)
        self.assertEqual(style.lookup('TNotebook.Tab','background',('selected',)),SELECTED)
        self.assertEqual(style.lookup('TNotebook.Tab','foreground',('selected',)),INK)
        self.assertEqual(style.lookup('TNotebook.Tab','background'),RAISED)


if __name__=='__main__':
    unittest.main()
