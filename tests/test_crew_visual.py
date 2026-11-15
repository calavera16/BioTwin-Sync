"""Graphical navigation and hidden mapped geometry, not visual approval."""
import copy
from datetime import datetime, timezone
import tkinter as tk
import unittest
from unittest.mock import patch

from biotwin.desktop import DesktopApp
from biotwin.crew_visual import CrewImages, draw_crew, draw_path, path_point
from biotwin.desktop_animation import packet_progress


class CrewVisualTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.withdraw(); self.errors=[]
        self.root.report_callback_exception=lambda *args,errors=self.errors:errors.append(args)
        with patch.object(DesktopApp,'discover_references'):
            self.app=DesktopApp(self.root,reduced_motion=True)
            self.root.update()
        self.root.attributes('-alpha',0.0); self.root.geometry('1050x720+20000+20000')
        self.root.deiconify(); self.settle()

    def settle(self):
        for _ in range(3): self.root.update()

    def tearDown(self):
        self.app.close(); self.assertFalse(self.errors)

    def inside(self,widget):
        self.assertTrue(widget.winfo_ismapped())
        self.assertGreater(widget.winfo_height(),20)
        self.assertGreaterEqual(widget.winfo_rootx(),self.root.winfo_rootx())
        self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),self.root.winfo_rootx()+self.root.winfo_width())
        self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height()-20)

    def test_figure_hits_open_exact_existing_details_without_mutating_evidence(self):
        a=self.app; c=a.controller; c.start_local()
        c.capture_manual(dict(subject_id=c.subject,source_id='QA instrument',metric='heart_rate',value=75,
            unit='bpm',observed_at=datetime.now(timezone.utc).isoformat(),method='QA entered reading',quality='usable'))
        a.refresh(); before=copy.deepcopy(c.document())
        for region in ('head','heart','vital'):
            with self.subTest(region=region):
                a.open_monitor('Crew 001'); self.settle()
                x,y=a.figure_targets[region]
                a.condition_cue.event_generate('<Motion>',x=round(x),y=round(y))
                self.settle()
                a.condition_cue.event_generate('<Button-1>',x=round(x),y=round(y))
                self.settle()
                self.assertTrue(a.measurements_open)
                self.assertEqual(a.selected_region,region)
                if region=='vital':
                    self.assertEqual(a.metric_name.cget('text'),'HEART RATE')
                    self.assertEqual(a.metric_value.cget('text'),'75 bpm')
                self.assertFalse(a.signal_playing)
                self.assertEqual(a.controller.document(),before)

    def test_native_keyboard_navigation_has_focus_help_and_routes_all_categories(self):
        a=self.app; a.open_monitor('Crew 001'); self.settle()
        for region,button in a.region_buttons.items():
            a.set_measurements(False); self.settle()
            self.inside(button)
            self.assertEqual(str(button),str(self.root.tk.call('tk_focusNext',str(button.tk_focusPrev()))))
            button.focus_force(); self.settle()
            self.assertEqual(a.figure_focus,region)
            self.assertTrue(a.condition_cue.find_withtag(region))
            button.event_generate('<space>')
            self.root.after(130,self.root.quit); self.root.mainloop(); self.settle()
            self.assertEqual(a.selected_region,region)
            self.assertTrue(a.measurements_open)
        a.set_measurements(False); self.settle()
        a.communication.focus_force(); a.communication.event_generate('<Return>'); self.settle()
        self.assertTrue(a.earth_review_open)

    def test_home_and_condition_scope_action_and_targets_fit_all_supported_sizes(self):
        a=self.app
        for size in ('1050x720','1280x800','1366x768'):
            with self.subTest(size=size):
                self.root.geometry(size+'+20000+20000'); a.show_home(); self.settle()
                first,second=[a.crew_cards[name] for name in ('Crew 001','Crew 002')]
                self.assertLess(first['area'].winfo_rootx()+first['area'].winfo_width(),second['area'].winfo_rootx())
                for card in (first,second): self.inside(card['open']); self.inside(card['state'])
                a.open_monitor('Crew 001'); self.settle()
                for mode in ('green','unknown','urgent'):
                    if mode=='unknown': a.controller.start_local(); a.refresh()
                    elif mode=='urgent':
                        c=a.controller; path=c.assistant_catalog()['protocols'][0]
                        q=next(q['id'] for q in path['questions'] if q['kind']=='urgent')
                        c.assess_assistant(path['id'],{q:'yes'},expected_context=c.assistant_context()); a.refresh()
                    self.settle()
                    self.inside(a.assistant_button); self.inside(a.communication)
                    for button in a.region_buttons.values(): self.inside(button)
                    c=a.condition_cue
                    scope=c.bbox('condition-scope'); title=c.bbox('condition-title')
                    self.assertLessEqual(scope[2],c.winfo_width())
                    self.assertLess(title[3],scope[1])
                    self.assertLessEqual(c.winfo_rooty()+scope[3],a.action_panel.winfo_rooty())
                    self.assertLessEqual(a.action_panel.winfo_rooty()+a.action_panel.winfo_height(),a.measurement_mode_label.winfo_rooty())
                a.controller.reset(); a.refresh()

    def test_repeated_figure_redraws_keep_callback_registration_bounded(self):
        a=self.app; a.open_monitor('Crew 001'); self.settle()
        before=len(a.condition_cue._tclCommands)
        document=copy.deepcopy(a.controller.document())
        for i in range(30): a.draw_condition(i/29)
        self.assertEqual(len(a.condition_cue._tclCommands),before)
        self.assertEqual(a.controller.document(),document)

    def test_directional_packet_position_tracks_real_timeline_and_received_state(self):
        a=self.app; a.open_monitor('Crew 001'); a.controller.seek_seconds(600); a.refresh(); self.settle()
        c=a.communication; packet=next(p for p in a.model['narrator_timeline'] if p['kind']=='packet')
        now=datetime.fromisoformat(a.controller.current.at).timestamp()
        start,end=[datetime.fromisoformat(packet[k]).timestamp() for k in ('at','arrives_at')]
        f=packet_progress(start,end,now); self.assertEqual(f,.5)
        expected=path_point(f,c.winfo_width(),max(45,c.winfo_height()-144),offset_y=140)
        bounds=c.coords(c.find_withtag('packet')[0])
        self.assertAlmostEqual((bounds[0]+bounds[2])/2,expected[0])
        self.assertAlmostEqual((bounds[1]+bounds[3])/2,expected[1])
        self.assertFalse(a.projection['doctor']['digest'])
        p0,p1,p2=[path_point(t,c.winfo_width(),c.winfo_height()) for t in (0,.5,1)]
        self.assertEqual(p0[1],p1[1]); self.assertEqual(p1[1],p2[1])
        self.assertLess(p0[0],p1[0]); self.assertLess(p1[0],p2[0])
        a.controller.seek_seconds(1200); a.refresh(); self.assertTrue(a.projection['doctor']['digest'])

    def test_compact_body_shares_navigation_and_stays_neutral_for_urgent_evidence(self):
        a=self.app; a.open_monitor('Crew 001'); a.set_measurements(True); self.settle()
        before=copy.deepcopy(a.controller.document())
        for region in ('head','heart','vital'):
            x,y=a.body_targets[region]
            a.body.event_generate('<Button-1>',x=round(x),y=round(y)); self.settle()
            self.assertEqual(a.selected_region,region)
            self.assertEqual(a.controller.document(),before)
            self.assertTrue(a.body.find_withtag('crew-silhouette'))
        self.assertFalse(a.body.find_withtag('crew-guide'))

    def test_reverse_progress_and_separate_lanes_settle_without_fake_traffic(self):
        c=self.app.communication; c.delete('all')
        forward=draw_path(c,300,45,lane=0,fraction=.25,offset_y=140)
        reverse=draw_path(c,300,45,lane=1,fraction=.25,reverse=True,offset_y=140)
        self.assertEqual(forward,path_point(.25,300,45,offset_y=140))
        self.assertEqual(reverse,path_point(.75,300,45,lane=1,offset_y=140))
        self.assertGreater(reverse[0],forward[0]); self.assertEqual(reverse[1]-forward[1],16)
        c.delete('all'); draw_path(c,300,45,fraction=1)
        self.assertFalse(c.find_withtag('packet')); self.assertTrue(c.find_withtag('packet-delivered'))
        c.delete('all'); draw_path(c,300,45)
        self.assertFalse(c.find_withtag('packet')); self.assertFalse(c.find_withtag('packet-delivered'))

    def test_actual_earth_reply_moves_toward_crew_before_receipt(self):
        a=self.app; a.open_monitor('Crew 001'); a.controller.seek_seconds(1500); a.refresh(); self.settle()
        c=a.communication
        packet=next(p for p in a.model['narrator_timeline'] if p['kind']=='packet' and p['sender']=='doctor')
        now=datetime.fromisoformat(a.controller.current.at).timestamp()
        sent,arrival=[datetime.fromisoformat(packet[k]).timestamp() for k in ('at','arrives_at')]
        fraction=packet_progress(sent,arrival,now); self.assertEqual(fraction,.25)
        expected=path_point(fraction,c.winfo_width(),max(45,c.winfo_height()-144),reverse=True,lane=1,offset_y=140)
        bounds=c.coords(c.find_withtag('packet')[0])
        self.assertAlmostEqual((bounds[0]+bounds[2])/2,expected[0])
        self.assertAlmostEqual((bounds[1]+bounds[3])/2,expected[1])
        self.assertFalse(a.projection['crew']['checklist']['original_reply'])
        a.controller.seek_seconds(2400); a.refresh()
        self.assertTrue(a.projection['crew']['checklist']['original_reply'])

    def test_artwork_cache_is_bounded_reused_and_missing_asset_has_readable_fallback(self):
        from pathlib import Path
        import tempfile
        a=self.app
        first,height=a.crew_images.get(188)
        self.assertIsNotNone(first)
        for _ in range(30): self.assertIs(a.crew_images.get(188)[0],first)
        self.assertLessEqual(len(a.crew_images.images),50)
        broken=CrewImages(self.root)
        with patch('biotwin.crew_visual.tk.PhotoImage',side_effect=tk.TclError('invalid PNG')) as decode:
            self.assertIsNone(broken.get(188)[0])
            self.assertIsNone(broken.get(188)[0])
            self.assertEqual(decode.call_count,1)
        broken.close()
        with tempfile.TemporaryDirectory() as directory:
            missing=CrewImages(self.root,Path(directory))
            self.assertIsNotNone(missing.error)
            c=a.body; c.delete('all'); draw_crew(c,60,6,100,images=missing)
            self.assertTrue(c.find_withtag('crew-unavailable'))
            missing.close(); self.assertIsNone(missing.master)


if __name__=='__main__': unittest.main()
