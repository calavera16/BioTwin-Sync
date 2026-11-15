import copy
import json
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from biotwin.desktop import DesktopApp
from biotwin.desktop_controller import DesktopController, instant
from biotwin.protocol import make_snapshot
from biotwin.workflow import Workflow
from biotwin.reference_library import load_reference_library


class MissionUITests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk()
        self.root.withdraw()
        self.errors=[]
        self.root.report_callback_exception=lambda *args,errors=self.errors:errors.append(args)
        with patch('biotwin.desktop.DesktopApp.discover_references'):
            self.app=DesktopApp(self.root, startup=False)
            self.root.update()

    def tearDown(self):
        if not self.app.closed:
            self.app.close()
        self.assertFalse(self.errors)

    def messages_text(self):
        original=tk.Toplevel
        def hidden(*args,**kw):
            w=original(*args,**kw);w.withdraw();return w
        with patch('biotwin.desktop.tk.Toplevel',new=hidden):
            self.app.show_messages()
        window=next(w for w in self.root.winfo_children() if isinstance(w,original) and w is not self.app.checklist_window)
        def texts(w):
            result=[]
            for child in w.winfo_children():
                if isinstance(child,tk.Text): result.append(child.get('1.0','end'))
                result.extend(texts(child))
            return result
        value=''.join(texts(window));window.destroy();return value

    def test_home_monitor_changed_reply_and_rewind(self):
        a=self.app
        self.assertEqual(a.screen,'home')
        self.assertEqual(len(a.crew_cards),2)
        self.assertIn('NO CHANGE IN RECORDED REACTION CHECK',a.crew_cards['Crew 001']['state'].cget('text'))
        a.open_monitor('Crew 001')
        self.assertEqual(a.metric_value.cget('text'),'420 ms')
        self.assertEqual(a.earth_reading.cget('text'),'— awaiting')
        a.jump(2)
        self.assertIn('NEEDS REVIEW',a.crew_cards['Crew 001']['state'].cget('text'))
        self.assertEqual(a.metric_value.cget('text'),'585 ms')
        self.assertEqual(a.earth_reading.cget('text'),'420 ms')
        self.assertEqual(len(a.step_widgets),0)
        self.assertNotIn('Confirm acquisition conditions',self.messages_text())
        a.jump(3)
        self.assertIn('PLAN B',a.guide_title.cget('text'))
        self.assertIn('420 ms',a.comparison.cget('text'))
        self.assertIn('585 ms',a.comparison.cget('text'))
        self.assertEqual(len(a.step_widgets),3)
        self.assertIn('Confirm acquisition conditions',self.messages_text())
        a.jump(4)
        self.assertIn('3/3',a.completion_label.cget('text'))
        a.jump(5)
        self.assertIn('Prior completions retained',a.selected_step.cget('text'))
        a.jump(7)
        self.assertEqual(a.earth_reading.cget('text'),'560 ms')
        a.jump(0)
        self.assertEqual(len(a.step_widgets),0)
        self.assertEqual(a.earth_reading.cget('text'),'— awaiting')

    def test_unknown_current_evidence_and_missing_channel_remain_unknown(self):
        a=self.app
        events=copy.deepcopy(a.controller.current.workflow.events)
        event=next(e for e in events if e['event_id']=='04-change')
        payload=event['snapshot']['payload']
        rows=[{**o,'value':None,'quality':'missing'} if o['id']=='changed' else o for o in payload['observations']]
        event['snapshot']=make_snapshot(payload['subject_id'],rows,payload['created_at'])
        a.controller.current.workflow=Workflow(events[:4])
        a.open_monitor('Crew 001')
        a.jump(3)
        self.assertEqual(a.metric_value.cget('text'),'—')
        self.assertIn('unavailable',a.comparison.cget('text').lower())
        self.assertIn('PLAN UNKNOWN',a.guide_title.cget('text'))
        a.open_monitor('Crew 002')
        self.assertIsNone(a.signal_panels['BVP']['record'])
        a.select_region('heart')
        self.assertEqual(a.metric_value.cget('text'),'ECG')
        self.assertIn('NASA',a.metric_badge.cget('text'))
        self.assertTrue(any(a.history.type(i)=='line' for i in a.history.find_all()))
        self.assertIn('unavailable',a.metric_baseline.cget('text'))
        a.select_region('head')
        self.assertEqual(a.metric_badge.cget('text'),'SYNTHETIC MISSION')
        self.assertEqual(a.metric_value.cget('text'),'435 ms')

    def test_operator_explicit_completion_followup_and_save(self):
        a=self.app
        a.controller.reset(interactive=True)
        a.open_monitor('Crew 001')
        a.controller.capture(585);a.controller.send_consultation();a.controller.advance()
        a.controller.author_reply('le',450,'Record a supported repeat.','Confirm device','Repeat measurement\nRecord context')
        a.controller.advance();a.refresh()
        self.assertEqual(len(a.step_widgets),2)
        self.assertEqual(a.projection['crew']['checklist']['completion_count'],0)
        a.select_step(0)
        a.note.insert(0,'Operator recorded a repeat with the same device.')
        a.complete()
        self.assertEqual(a.projection['crew']['checklist']['completion_count'],1)
        a.controller.followup();a.refresh()
        self.assertFalse(a.projection['doctor']['followups'])
        a.controller.advance();a.refresh()
        self.assertEqual(len(a.projection['doctor']['followups']),1)
        with tempfile.TemporaryDirectory() as folder:
            document=a.controller.document()
            path=a.controller.save(folder)
            a.controller.open(path)
            a.refresh()
            self.assertEqual(a.controller.document(),document)

    def test_draft_cannot_be_retargeted_by_switching_crew(self):
        a=self.app
        a.controller.reset(interactive=True)
        a.controller.send_consultation();a.controller.advance();a.refresh()
        original_toplevel=tk.Toplevel
        def hidden(*args,**kw):
            w=original_toplevel(*args,**kw);w.withdraw();return w
        with patch('biotwin.desktop.tk.Toplevel',new=hidden):
            a.author_dialog()
        window=next(w for w in self.root.winfo_children() if isinstance(w,original_toplevel) and w is not a.checklist_window)
        def descendants(w):
            for child in w.winfo_children():
                yield child
                yield from descendants(child)
        send=next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='Send authored checklist')
        a.open_monitor('Crew 002')
        before=copy.deepcopy(a.controller.current.workflow.events)
        with patch('biotwin.desktop.messagebox.showerror') as error:
            send.invoke()
            self.assertIn('Crew 001',error.call_args.args[1])
        self.assertEqual(a.controller.current.workflow.events,before)
        window.destroy()

    def test_long_accepted_checklist_scrolls_with_footer_accessible(self):
        a=self.app
        a.controller.reset(interactive=True)
        a.controller.capture(585);a.controller.send_consultation();a.controller.advance()
        lines='\n'.join('Step '+str(i)+' '+('record measurement context '*17) for i in range(6))
        a.controller.author_reply('le',450,'Record a supported repeat.',lines,lines)
        a.controller.advance();a.open_monitor('Crew 001');self.root.update_idletasks()
        a.select_step(0); self.root.update_idletasks()
        self.assertEqual(len(a.step_widgets),6)
        self.root.attributes('-alpha',0.0); self.root.geometry('1050x720+20000+20000'); self.root.deiconify()
        a.checklist_window.attributes('-alpha',0.0); a.checklist_window.geometry('640x530+20000+20000')
        a.open_checklist(); self.root.update()
        self.assertGreaterEqual(a.steps_canvas.winfo_height(),150)
        bottom=a.complete_button.winfo_rooty()-a.checklist_window.winfo_rooty()+a.complete_button.winfo_height()
        self.assertLess(bottom,a.checklist_window.winfo_height())
        self.assertGreater(a.complete_button.winfo_height(),20)
        a.reveal_step(5)
        self.assertGreater(a.steps_canvas.yview()[0],0)

    def test_reference_cards_and_network_failure_keep_monitor_usable(self):
        a=self.app
        a.reference_result=load_reference_library('data/raw/nlsp')
        a.draw_references()
        text=[a.reference_canvas.itemcget(i,'text') for i in a.reference_canvas.find_all() if a.reference_canvas.type(i)=='text']
        self.assertTrue(any('C3F:' in t for t in text))
        a.load_queue.put(('error','test offline'))
        a.root.after_cancel(a.poll_job)
        a.poll_load()
        self.assertIn('unavailable',a.reference_status.cget('text'))
        a.open_monitor('Crew 001');a.jump(3)
        self.assertIn('PLAN B',a.guide_title.cget('text'))

    def test_local_tools_manual_tiles_and_timing_cancellation(self):
        from datetime import datetime,timezone
        a=self.app; a.controller.start_local(); a.open_monitor('Crew 001')
        self.assertEqual(a.metric_badge.cget('text'),'LAPTOP CHECK')
        self.assertEqual(a.metric_value.cget('text'),'—')
        a.controller.capture_manual({'subject_id':a.controller.subject,'metric':'heart_rate','value':75,'unit':'bpm',
            'source_id':'manual','method':'entered instrument reading','quality':'usable',
            'observed_at':datetime.now(timezone.utc).isoformat()}); a.refresh()
        self.assertIn('75',a.sensor_tiles['HR'][1].cget('text'))
        self.assertIn('Operator recorded',a.sensor_tiles['HR'][2].cget('text'))
        original=tk.Toplevel
        def hidden(*args,**kw):
            window=original(*args,**kw); window.withdraw(); return window
        with patch('biotwin.desktop.tk.Toplevel',new=hidden):
            a.local_tools(); a.reaction_dialog()
        self.root.update_idletasks()
        before=copy.deepcopy(a.controller.current.workflow.events)
        a.local_cancels[-1]()
        self.assertEqual(before,a.controller.current.workflow.events)

    def test_plot_missing_and_review_quality_breaks_trace(self):
        a=self.app
        metric=copy.deepcopy(a.projection['crew']['metric'])
        metric['history']=metric['history'][:3]
        metric['history'][1]['quality']='review'
        metric['reference']={'status':'unavailable'}
        a.plot_history(a.history,metric)
        texts=[a.history.itemcget(i,'text') for i in a.history.find_all() if a.history.type(i)=='text']
        self.assertIn('×',texts)
        self.assertEqual(sum(a.history.type(i)=='oval' for i in a.history.find_all()),3)

    def test_model_demo_uses_actual_scores_and_keeps_personal_state_separate(self):
        a=self.app; before=a.controller.document(); original=tk.Toplevel
        def hidden(*args,**kw):
            window=original(*args,**kw); window.withdraw(); return window
        with patch('biotwin.desktop.tk.Toplevel',new=hidden): a.model_demo()
        window=a.local_windows[-1]
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(window))
        buttons={w.cget('text'):w for w in widgets if isinstance(w,ttk.Button)}
        output=next(w for w in widgets if isinstance(w,tk.Text))
        buttons['Load NASA ECG example'].invoke()
        source=json.loads(output.get('1.0','end'))
        self.assertTrue(source['source']['subject_id'].startswith('NASA:SOTERIA:'))
        self.assertTrue(any(w['technical_corruption_score'] is not None for w in source['analysis']['windows']))
        buttons['Create labelled corrupted demo'].invoke()
        demo=json.loads(output.get('1.0','end'))
        self.assertIn('controlled_injection_demo',demo['source'])
        self.assertNotEqual(demo['source']['source_id'],source['source']['source_id'])
        self.assertEqual(demo['source']['subject_id'],source['source']['subject_id'])
        buttons['Mark [start, end) samples'].invoke(); buttons['Queue repeat request'].invoke(); buttons['Resolve oldest request'].invoke()
        reviewed=json.loads(output.get('1.0','end'))
        self.assertEqual(len(reviewed['manual_review_marks']),1)
        self.assertEqual(reviewed['repeat_requests'][0]['status'],'resolved')
        self.assertEqual(before,a.controller.document())


if __name__=='__main__':
    unittest.main()
