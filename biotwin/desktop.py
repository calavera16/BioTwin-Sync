"""Astronaut health monitoring console with isolated, provenance-labelled evidence."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import queue
import sys
import tempfile
import threading
import time
import weakref
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from . import __version__
from .desktop_assets import load_examples, preview
from .desktop_animation import Playback, RecordTrace, packet_progress
from .desktop_controller import CREWS, STOPS, DesktopController, default_sessions_directory, guided_events
from .diagnostics import self_check
from .mission import mission_projection
from .viewmodel import view_model
from .desktop_presentation import (GUIDED_END_SECONDS, demo_facts, measurement_label, reference_label, evidence_summary, evidence_age, time_label, received_time)
from .data import finite
from .desktop_ux import FocusHelp, Introduction, FiniteMotion, ScrollableForm, form_text_navigation, gradient
from .crew_status import project_crew_status
from .crew_visual import CrewImages, draw_crew, draw_path, target_at
from .desktop_theme import (
    ACCENT, AMBER, BAND_INNER, BAND_OUTER, BASELINE, BG, BORDER, CYAN_DIM,
    EXCLUDED, GRID, GREEN, HOVER, HUD_BORDER, HUD_CARD, HUD_SURFACE, INK,
    MUTED, PANEL, PLOT, RAISED, RED, SELECTED, TRACE, apply_theme,
)

def clock(at):
    return datetime.fromisoformat(at).astimezone(timezone.utc).strftime('%H:%M:%S UTC')

def text_content(widget, value):
    widget.configure(state='normal'); widget.delete('1.0','end'); widget.insert('1.0',value); widget.configure(state='disabled')

def model_demo_signal(example):
    """Exact source arrays; observed_at marks loading, never a NASA exam date."""
    return {'id':'NASA-source-demo-'+example['file'],'subject_id':'NASA:SOTERIA:'+example['annotation']['participant'],
        'source_id':'NASA:SOTERIA:'+example['source_key'],'channel':example['channel'].upper(),
        'samples':list(example['values']),'sample_times_seconds':list(example['times']),
        'sample_rate_hz':example['nominal_rate_hz'],'unit':example['unit'],'quality':'review',
        'observed_at':datetime.now(timezone.utc).isoformat(),
        'observed_at_meaning':'Local demo loaded at; original calendar acquisition date unavailable',
        'method':'Exact bundled NASA excerpt loaded for separate model demo','time_basis':example['time_basis'],
        'provenance':'recorded NASA source example, separate from local personal evidence',
        'original_sample_start':example['sample_start'],'source_sha256':example['source_sha256']}

class DesktopApp:
    def __init__(self, root, *, controller=None, startup=False, reduced_motion=False):
        self.root=root; self.controller=controller or DesktopController()
        self.crew_images=CrewImages(root)
        self.playing=False; self.play_job=None; self.signal_playing=False; self.signal_job=None
        self.consult_clock=Playback(GUIDED_END_SECONDS,speed=120.0); self.last_consult_second=None
        self.setting_scale=False; self.role_highlights={}; self.load_queue=queue.Queue()
        self.closed=False; self.poll_job=None; self.startup_job=None; self.examples=[]; self.assets_error=None
        self.reference_result=None; self.selected_region='head'; self.selected_step_index=None; self.screen='home'
        self.local_windows=[]; self.local_cancels=[]; self.assistant_dialogs=[]
        self.presentation_host=None
        self.presentation_recovery=None
        self.introduction=None; self.intro_job=None; self.reduced_motion=reduced_motion
        self.measurements_open=False; self.earth_review_open=False; self.cue_signature=None
        self.feedback=FiniteMotion(root); self.feedback_progress=1.0
        self.selected_channel='ECG'; self.selected_vital='HR'; self.demo_open=False; self.action_open=False
        self.software_checks=self_check()
        try: self.examples=load_examples()
        except (OSError,ValueError,KeyError,TypeError) as exc: self.assets_error=str(exc)
        self.record_cache={e['file']:RecordTrace(e) for e in self.examples}
        root.title('BioTwin Sync | Astronaut Health Monitoring'); root.geometry('1280x800'); root.minsize(1050,720)
        root.configure(background=BG); root.protocol('WM_DELETE_WINDOW',self.close)
        apply_theme(root)
        header=ttk.Frame(root,padding=(18,5)); header.pack(fill='x')
        brand=ttk.Frame(header); brand.pack(side='left')
        ttk.Label(brand,text='BioTwin Sync',font=('Segoe UI',16,'bold'),foreground=INK).pack(side='left')
        ttk.Label(brand,text='DELAYED EARTH LINK',font=('Segoe UI',8,'bold'),foreground=TRACE).pack(side='left',padx=(12,0),pady=(3,0))
        self.help_menu=tk.Menu(root,tearoff=False)
        self.help_menu.add_command(label='Quick guide',command=self.show_introduction)
        self.help_menu.add_command(label='Chart help',command=self.chart_help)
        self.help_menu.add_command(label='Reduce motion / restore motion',command=self.toggle_motion)
        self.help_menu.add_command(label='About / data sources',command=self.about)
        self.session_menu=tk.Menu(root,tearoff=False)
        for label,command in [('Onboard assistant',self.onboard_assistant),('Save session',self.save),('Open session',self.open_session),('Export session',self.export),
                              ('New operator session',self.new_operator),('Local measurements',self.local_tools),('Reset guided scenario',self.reset_guided)]:
            self.session_menu.add_command(label=label,command=command)
        for label,menu in [('Help',self.help_menu),('Session',self.session_menu)]:
            button=ttk.Menubutton(header,text=label,menu=menu); button.pack(side='right',padx=6)
        self.demo_button=ttk.Button(header,text='Demo controls',style='Small.TButton',command=self.toggle_demo)
        self.demo_button.pack(side='right',padx=5)
        self.presentation_button=ttk.Button(header,text='Presentation',style='Small.TButton',command=self.open_presentation)
        self.presentation_button.pack(side='right',padx=5)
        self.status=tk.StringVar(master=root,value='Crew monitoring ready.')
        FocusHelp(self.presentation_button,self.status,'Open separate Crew and Flight surgeon browser screens with synthetic evidence. Native recorded sessions stay here. Shortcut: Ctrl+P.')
        ttk.Label(root,textvariable=self.status,padding=(18,5),foreground=MUTED,font=('Segoe UI',9)).pack(side='bottom',fill='x')
        self.quick_demo_button=ttk.Button(header,text='Play 09:00–09:40 demo',style='Hero.TButton',command=self.launch_guided_demo)
        self.quick_demo_button.pack(side='right',padx=(6,8))
        FocusHelp(self.quick_demo_button,self.status,'Play or pause the selected crew’s 09:00–09:40 guided scenario. Current progress is retained.')
        ttk.Label(header,text=' DEMO / OFFLINE ',foreground=AMBER,font=('Segoe UI',9,'bold')).pack(side='right',padx=12)
        self.build_timeline()
        self.content=ttk.Frame(root,padding=(16,8,16,0)); self.content.pack(fill='both',expand=True)
        self.home=ttk.Frame(self.content); self.monitor=ttk.Frame(self.content); self.crew_var=tk.StringVar(master=root,value=CREWS[0])
        self.build_home(); self.build_monitor(); self.home.pack(fill='both',expand=True)
        self.change_source(); self.refresh(); self.poll_job=root.after(200,self.poll_load)
        root.bind('<Control-s>',lambda _:self.save()); root.bind('<Control-o>',lambda _:self.open_session())
        root.bind('<Control-p>',lambda e:self.open_presentation() if e.widget.winfo_toplevel() is self.root else None)
        root.bind('<Alt-h>',lambda _:self.select_region('head')); root.bind('<Alt-c>',lambda _:self.select_region('heart'))
        root.bind('<Escape>',lambda e:self.show_home() if e.widget.winfo_toplevel() is self.root else None); self.startup_job=root.after_idle(self.discover_references)
        if startup: self.intro_job=root.after_idle(lambda:self.show_introduction(animate=True))

    def open_presentation(self, *, open_browser=True):
        """Separate synthetic browser session; the native controller stays untouched."""
        if self.closed:
            return None
        from .presentation import PresentationHost
        try:
            if self.presentation_host is None:
                candidate=PresentationHost()
                try: candidate.start()
                except Exception:
                    candidate.close()
                    raise
                self.presentation_host=candidate
            if open_browser:
                import webbrowser
                try: opened=webbrowser.open(self.presentation_host.url, new=2)
                except (OSError,webbrowser.Error): opened=False
                if not opened:
                    self.status.set('Presentation is ready, but no browser opened. Copy the address in the recovery window, or set a default browser in Windows Settings.')
                    self.show_presentation_recovery()
                    return self.presentation_host
                if self.presentation_recovery is not None and self.presentation_recovery.winfo_exists():
                    self.presentation_recovery.destroy(); self.presentation_recovery=None
            self.status.set('Presentation is open: separate synthetic crew / Flight surgeon session. Closing BioTwin stops its browser connection.'
                if open_browser else 'Presentation is ready: separate synthetic crew / Flight surgeon session. Closing BioTwin stops its browser connection.')
            return self.presentation_host
        except (OSError,ValueError) as exc:
            self.status.set('Presentation could not start: '+str(exc)[:160])
            return None

    def show_presentation_recovery(self):
        """Expose the capability URL only in an explicit, user-operated dialog."""
        if self.presentation_recovery is not None and self.presentation_recovery.winfo_exists():
            self.presentation_recovery.lift(); return self.presentation_recovery
        window=tk.Toplevel(self.root); self.presentation_recovery=window
        window.title('Open browser presentation'); window.geometry('660x270'); window.minsize(500,270)
        window.configure(bg=BG); window.transient(self.root)
        area=ttk.Frame(window,padding=20); area.pack(fill='both',expand=True)
        ttk.Label(area,text='Your presentation is ready',font=('Segoe UI',16,'bold')).pack(anchor='w')
        explanation=ttk.Label(area,text='Paste this local address into a browser on this computer. This is a separate synthetic session. Keep BioTwin Sync open to use it.',wraplength=610)
        explanation.pack(fill='x',pady=(10,12))
        address=ttk.Entry(area); address.insert(0,self.presentation_host.url); address.configure(state='readonly'); address.pack(fill='x')
        feedback=ttk.Label(area,text='The address grants access to this local presentation; share it only intentionally.',foreground=MUTED,wraplength=610)
        feedback.pack(fill='x',pady=(6,12))
        row=ttk.Frame(area); row.pack(fill='x')
        def copy():
            window.clipboard_clear(); window.clipboard_append(self.presentation_host.url)
            feedback.configure(text='Address copied. Paste it into your browser on this computer.')
        ttk.Button(row,text='Copy local address',style='Accent.TButton',command=copy).pack(side='left')
        ttk.Button(row,text='Try browser again',command=self.open_presentation).pack(side='left',padx=8)
        ttk.Button(row,text='Close',command=window.destroy).pack(side='right')
        window.bind('<Escape>',lambda _:window.destroy())
        area.bind('<Configure>',lambda e:[label.configure(wraplength=max(300,e.width-40)) for label in (explanation,feedback)])
        address.focus_set(); address.selection_range(0,'end')
        return window

    def toggle_demo(self):
        self.demo_open=not self.demo_open
        if self.demo_open:
            self.timeline_area.pack(fill='x',before=self.content); self.quick_demo_button.pack_forget()
        else:
            self.timeline_area.pack_forget(); self.quick_demo_button.pack(side='right',padx=(6,8))
        self.demo_button.configure(text='Hide demo' if self.demo_open else 'Demo controls')

    def toggle_motion(self):
        self.reduced_motion=not self.reduced_motion
        if self.reduced_motion:
            self.pause(); self.stop_signal(); self.feedback.cancel(); self.feedback_progress=1.0
            self.draw_condition()
        if self.introduction and not self.introduction.closed: self.introduction.finish_animation()
        self.status.set('Reduced motion enabled. Graph replay remains available on request.' if self.reduced_motion else 'Motion restored.')

    def show_introduction(self,animate=False):
        self.intro_job=None
        if self.introduction and not self.introduction.closed:
            self.introduction.window.lift(); return self.introduction
        # The guide does not seek, reset, change crew or modify medical evidence.
        self.introduction=Introduction(self.root,animate=animate,reduced_motion=self.reduced_motion,
            on_close=lambda _:setattr(self,'introduction',None),
            on_launch_demo=self.launch_guided_demo, facts=self.guided_facts())
        return self.introduction

    def guided_facts(self):
        events=guided_events(CREWS[0])
        initial=mission_projection(view_model(events,'2026-11-14T09:00:00+00:00'))['crew']['metric']
        changed=mission_projection(view_model(events,'2026-11-14T09:30:00+00:00'))['crew']['metric']
        return demo_facts(initial,changed)

    def launch_guided_demo(self):
        """Continue a guided session; restarting is an explicit separate action."""
        if self.playing:
            self.pause(); self.refresh(); return
        if self.controller.current.mode!='guided':
            self.status.set('Use Session > Reset guided scenario to start the synthetic demo. Current evidence is retained.')
            return
        if self.screen!='monitor': self.open_monitor(self.controller.selected)
        if not self.demo_open: self.toggle_demo()
        if self.consultation_seconds()>=GUIDED_END_SECONDS:
            self.status.set('Guided demo complete at 09:40. Choose 09:00 to replay, or Next extended event for later history.')
            return
        self.toggle_play()

    def chart_help(self):
        if self.selected_region=='heart': return self.source_details()
        if self.selected_region=='vital':
            return self.show_text('Recorded measurement','Only explicitly entered or imported measurements appear here. An unavailable value is not inferred from NASA replay. No comparable time history is currently available for this view. Use Measure to review source, method, time and quality before recording.')
        self.show_text('Read the reaction-time graph',
            'Reaction time is measured in milliseconds (ms); the horizontal axis is observation time in UTC. '
            'A higher value means a slower recorded response. Light-blue points are usable observations. '
            'A cross marks missing, review-quality or conflicting evidence and breaks the line.\n\n'
            'Shading is the earlier comparable history spread: inner band ±1 SD, outer band ±2 SD; '
            'the dashed line is the earlier mean. These are descriptive references, not healthy or safe ranges. '
            'Attention asks for review, not a diagnosis. The source badge distinguishes synthetic mission observations from local checks.')

    def card(self,parent,padding=12):
        return ttk.Frame(parent,style='Card.TFrame',padding=padding)

    def build_timeline(self):
        area=ttk.Frame(self.root,padding=(18,2,18,3)); self.timeline_area=area
        row=ttk.Frame(area); row.pack(fill='x')
        self.play_button=ttk.Button(row,text='Play demo',command=self.toggle_play,width=13); self.play_button.pack(side='left')
        self.step_button=ttk.Button(row,text='Next extended event',command=self.step); self.step_button.pack(side='left',padx=5)
        self.consult_speed=ttk.Combobox(row,state='readonly',values=('60x','120x','240x'),width=5)
        self.consult_speed.set('120x'); self.consult_speed.pack(side='left',padx=(0,12)); self.consult_speed.bind('<<ComboboxSelected>>',self.change_consult_speed)
        self.stage_buttons=[]
        for index,label in ((0,'09:00  Sent'),(1,'09:20  Earth'),(2,'09:30  Change'),(3,'09:40  Guide')):
            b=ttk.Button(row,text=label,command=lambda i=index:self.jump(i),style='Small.TButton'); b.pack(side='left',padx=3); self.stage_buttons.append(b)
        self.clock_label=ttk.Label(row,font=('Consolas',17,'bold'),foreground=INK); self.clock_label.pack(side='right')
        self.consult_position=tk.DoubleVar(master=self.root,value=0)
        self.consult_scale=ttk.Scale(area,from_=0,to=GUIDED_END_SECONDS,variable=self.consult_position,command=self.scrub_consult); self.consult_scale.pack(fill='x',pady=(3,0))
        self.timeline_summary=ttk.Label(area,foreground=MUTED,font=('Segoe UI',8)); self.timeline_summary.pack(anchor='w')

    def build_home(self):
        row=ttk.Frame(self.home); row.pack(fill='x',pady=(6,2))
        ttk.Label(row,text='Your crew.',font=('Segoe UI',30,'bold')).pack(side='left')
        ttk.Button(row,text='Test alert',command=self.test_alert,style='Small.TButton').pack(side='right')
        ttk.Label(self.home,text='Choose crew, understand the change, then take the next step. Earth replies arrive after a simulated delay.',foreground=MUTED,font=('Segoe UI',11)).pack(anchor='w',pady=(0,6))
        destinations=ttk.Frame(self.home); destinations.pack(fill='both',expand=True)
        destinations.columnconfigure(0,weight=50,uniform='roster'); destinations.columnconfigure(1,weight=50,uniform='roster'); destinations.rowconfigure(0,weight=1)
        self.crew_cards={}
        for index,crew in enumerate(CREWS):
            area=ttk.Frame(destinations,padding=(10,8)); area.grid(row=0,column=index,sticky='nsew',padx=(0,12) if index==0 else (12,0),pady=0)
            chart=tk.Canvas(area,height=340,bg=BG,highlightthickness=0,cursor='hand2')
            chart.pack(fill='both',expand=True); chart.bind('<Configure>',lambda _,c=crew:self.draw_roster(c)); chart.bind('<Button-1>',lambda _,c=crew:self.open_monitor(c))
            FocusHelp(chart,self.status,'Click card to open '+crew+' telemetry monitor.')
            state=ttk.Label(area,font=('Segoe UI',10,'bold'),wraplength=410); state.pack(anchor='w',pady=(6,10))
            button=ttk.Button(area,text='Open '+crew,command=lambda c=crew:self.open_monitor(c),style='Accent.TButton' if index==0 else 'TButton')
            button.pack(anchor='w',pady=(0,6))
            FocusHelp(button,self.status,'Open '+crew+' / a separate session, condition cue and next action.')
            value=ttk.Label(area); detail=ttk.Label(area); note=ttk.Label(area)
            self.crew_cards[crew]=dict(state=state,value=value,detail=detail,chart=chart,note=note,open=button,area=area)
            area.bind('<Configure>',lambda e,label=state:label.configure(wraplength=max(220,e.width-28)))
        link=ttk.Frame(self.home,padding=(12,10)); link.pack(fill='x')
        ttk.Label(link,text='EARTH LINK  /',font=('Segoe UI',10,'bold'),foreground=TRACE).pack(side='left')
        self.home_link=ttk.Label(link,foreground=MUTED); self.home_link.pack(side='left',padx=14)
        ttk.Button(link,text='Open communications',command=lambda:(self.open_monitor(self.controller.selected),self.set_earth_review(True))).pack(side='right')

    def build_monitor(self):
        nav=ttk.Frame(self.monitor); nav.pack(fill='x',pady=(0,5))
        ttk.Button(nav,text='← Crew overview',command=self.show_home,style='Small.TButton').pack(side='left')
        self.crew_combo=ttk.Combobox(nav,values=CREWS,state='readonly',width=10,textvariable=self.crew_var); self.crew_combo.pack(side='left',padx=8); self.crew_combo.bind('<<ComboboxSelected>>',lambda _:self.select_crew())
        self.monitor_status=ttk.Label(nav,font=('Segoe UI',10,'bold')); self.monitor_status.pack(side='left',padx=8)
        self.mode_label=ttk.Label(nav,foreground=MUTED,font=('Segoe UI',8,'bold')); self.mode_label.pack(side='right',padx=(6,0))
        self.quick_stage_buttons=[]
        grid=ttk.Frame(self.monitor); grid.pack(fill='both',expand=True); grid.columnconfigure(0,weight=68,minsize=620,uniform="monitor-columns"); grid.columnconfigure(1,weight=32,minsize=310,uniform="monitor-columns"); grid.rowconfigure(0,weight=1)
        left=ttk.Frame(grid); left.grid(row=0,column=0,sticky='nsew',padx=(0,10)); left.columnconfigure(0,weight=1); left.rowconfigure(0,weight=1)
        right=self.card(grid,10); right.grid(row=0,column=1,sticky='nsew',pady=(28,0))
        top=self.card(left,10); top.grid(row=0,column=0,sticky='nsew')
        self.condition_panel=top; self.earth_panel=right
        title=ttk.Frame(top,style='Card.TFrame'); title.pack(fill='x',pady=(0,4))
        ttk.Label(title,text='Current observation',style='CardMuted.TLabel',font=('Segoe UI',9,'bold')).pack(side='left')
        self.measurements_toggle=ttk.Button(title,text='View measurements',style='Small.TButton',command=self.toggle_measurements); self.measurements_toggle.pack(side='right',padx=5)
        ttk.Button(title,text='Measure',style='Small.TButton',command=self.local_tools).pack(side='right')
        ttk.Button(title,text='Why this result?',style='Small.TButton',command=lambda:self.show_role('crew')).pack(side='right',padx=5)
        self.condition_cue=tk.Canvas(top,height=300,bg=BG,highlightthickness=0)
        self.condition_cue.pack(fill='both',expand=True,pady=(10,0)); self.condition_cue.bind('<Configure>',lambda _:self.draw_condition())
        self.figure_controls=ttk.Frame(top)
        self.region_buttons={}
        for region,label,help_text in (
            ('head','Cognition  [Alt+H]','Open recorded reaction-time history; category navigation, not a head diagnosis.'),
            ('heart','NASA recordings  [Alt+C]','Open separate NASA ECG/BVP source recordings. Playback begins only when you press Play.'),
            ('vital','Measurements','Open supplied measurements or diary entries. Missing evidence is never inferred.')):
            button=ttk.Button(self.figure_controls,text=label,style='Small.TButton',command=lambda r=region:self.select_region(r))
            button.pack(fill='x',pady=2); self.region_buttons[region]=button
            FocusHelp(button,self.status,help_text)
            button.bind('<FocusIn>',lambda _,r=region:self.focus_region(r),add='+')
            button.bind('<FocusOut>',lambda _:self.focus_region(None),add='+')
        self.figure_controls.place(relx=.02,rely=.72,relwidth=.40)
        self.figure_focus=None; self.figure_targets={}
        self.bind_figure(self.condition_cue)
        self.evidence_frame=ttk.Frame(top)
        evidence=self.evidence_frame
        anatomy=ttk.Frame(evidence,style='Card.TFrame',width=142); anatomy.pack(side='left',fill='y'); anatomy.pack_propagate(False)
        ttk.Label(anatomy,text='BODY SYSTEMS',style='CardMuted.TLabel',font=('Segoe UI',8,'bold')).pack(anchor='w')
        self.body=tk.Canvas(anatomy,bg=BG,height=175,width=136,highlightthickness=0); self.body.pack(fill='both',expand=True); self.body.bind('<Configure>',lambda _:self.draw_body())
        self.bind_figure(self.body)
        ttk.Button(anatomy,text='Head / cognition',style='Small.TButton',command=lambda:self.select_region('head')).pack(fill='x',pady=2)
        ttk.Button(anatomy,text='Heart / signals',style='Small.TButton',command=lambda:self.select_region('heart')).pack(fill='x')
        metric=ttk.Frame(evidence,style='Card.TFrame',padding=(12,0,0,0)); metric.pack(side='left',fill='both',expand=True)
        heading=ttk.Frame(metric,style='Card.TFrame'); heading.pack(fill='x')
        self.nasa_banner=ttk.Label(metric,text='NASA reference recording — separate from this crew’s observations\nSource: NASA SOTERIA · amplitude units unverified · not sent as crew evidence',foreground=AMBER,wraplength=480,font=('Segoe UI',11,'bold')); self.nasa_banner.pack(fill='x',pady=(5,3))
        self.metric_name=ttk.Label(heading,text='COGNITIVE RESPONSE',style='CardMuted.TLabel',font=('Segoe UI',9,'bold')); self.metric_name.pack(side='left')
        self.metric_badge=ttk.Label(heading,text='SYNTHETIC MISSION',style='CardMuted.TLabel',font=('Segoe UI',7)); self.metric_badge.pack(side='right')
        reading=ttk.Frame(metric,style='Card.TFrame'); reading.pack(fill='x'); self.metric_reading=reading
        self.metric_value=ttk.Label(reading,style='Card.TLabel',font=('Consolas',23,'bold')); self.metric_value.pack(side='left')
        self.metric_change=ttk.Label(reading,style='Card.TLabel',font=('Segoe UI',10,'bold')); self.metric_change.pack(side='right')
        self.metric_baseline=ttk.Label(metric,style='CardMuted.TLabel',font=('Segoe UI',9)); self.metric_baseline.pack(anchor='w')
        self.history=tk.Canvas(metric,height=195,bg=PLOT,highlightthickness=0,takefocus=True); self.history.pack(fill='both',expand=True,pady=(6,2)); self.history.bind('<Configure>',lambda _:self.draw_history('crew'))
        FocusHelp(self.history,self.status,'Chart help explains units, timestamps, gaps and earlier-history shading. Enter opens it.')
        self.history.bind('<Return>',lambda _:self.chart_help())
        self.chart_legend=ttk.Label(metric,text='Time (UTC) · ms  |  Bands: earlier history ±1 / ±2 SD',style='CardMuted.TLabel',font=('Segoe UI',8)); self.chart_legend.pack(anchor='w',pady=(3,5))
        self.signals=ttk.Frame(metric,style='Card.TFrame'); self.signals.pack(fill='x'); self.build_signals()
        sensors=ttk.Frame(metric,style='Card.TFrame'); sensors.pack(fill='x',pady=(6,0))
        ttk.Label(sensors,text='Other measurements',style='CardMuted.TLabel',font=('Segoe UI',9)).pack(side='left')
        self.vital_selector=ttk.Combobox(sensors,state='readonly',values=('Heart rate','Blood pressure','Oxygen saturation','Temperature','Sleep'),width=18)
        self.vital_selector.set('Choose a measurement'); self.vital_selector.pack(side='right')
        self.vital_selector.bind('<<ComboboxSelected>>',lambda _:self.select_vital())
        self.sensor_tiles={}
        for name,key in (('HR','HR'),('BP','BP'),('SpO₂','SpO2'),('TEMP','temperature'),('SLEEP','sleep')):
            col=ttk.Frame(sensors,style='Card.TFrame')
            value=ttk.Label(col,text=name+'  —',style='Card.TLabel',font=('Segoe UI',9,'bold')); value.pack(anchor='w')
            source=ttk.Label(col,text='Unavailable',style='CardMuted.TLabel',font=('Segoe UI',7)); source.pack(anchor='w')
            self.sensor_tiles[key]=(name,value,source)
        # Reserve the control rows; only the graphical surface shrinks.
        for container,flex in ((anatomy,self.body),(metric,self.history)):
            container.columnconfigure(0,weight=1)
            children=[(w,w.pack_info()) for w in container.winfo_children()]
            for widget,_ in children: widget.pack_forget()
            for index,(widget,info) in enumerate(children):
                widget.grid(row=index,column=0,sticky='nsew',pady=info.get('pady',0))
                if widget is flex: container.rowconfigure(index,weight=1)
        self.nasa_banner.grid_remove()
        self.signals.grid_remove()
        self.build_guidance(top); self.build_surgeon(right)
        self.measurement_mode_label=ttk.Label(top,foreground=MUTED,font=('Segoe UI',9)); self.measurement_mode_label.pack(anchor='w',pady=(8,0))
        metric.bind('<Configure>',lambda e:(self.nasa_banner.configure(wraplength=max(200,e.width-12)),self.metric_baseline.configure(wraplength=max(200,e.width-12)),self.chart_legend.configure(wraplength=max(200,e.width-12))))
        self.action_panel.bind('<Configure>',lambda e:self.next_action_label.configure(wraplength=max(180,e.width-16)))

    def build_guidance(self,parent):
        area=self.card(parent,8); self.action_panel=area
        area.place(relx=.46,rely=.62,relwidth=.52,relheight=.34)
        ttk.Label(area,text='NEXT STEP',style='CardMuted.TLabel',font=('Segoe UI',10,'bold')).pack(anchor='w')
        self.assistant_button=ttk.Button(area,text='Open onboard assistant',style='Accent.TButton',command=self.next_action)
        self.assistant_button.pack(anchor='w',pady=(6,0))
        self.next_action_label=ttk.Label(area,style='Card.TLabel',font=('Segoe UI',11),wraplength=310)
        self.next_action_label.pack(anchor='w',pady=(6,4))
        self.completion_label=ttk.Label(area,style='CardMuted.TLabel',font=('Segoe UI',10)); self.completion_label.pack(anchor='w')
        self.checklist_button=ttk.Button(area,text='Open received checklist',command=self.open_checklist,style='Small.TButton')
        self.checklist_button.pack(anchor='w',pady=(5,0))
        FocusHelp(self.assistant_button,self.status,'Open the complete received checklist or onboard help. Completion is always explicitly recorded.')
        window=tk.Toplevel(self.root); self.checklist_window=window; self.checklist_binding=None; window.withdraw()
        window.title('Received checklist'); window.geometry('850x650'); window.minsize(640,530)
        window.configure(bg=BG); window.transient(self.root); window.protocol('WM_DELETE_WINDOW',self.close_checklist)
        window.bind('<Escape>',lambda _: (self.close_checklist(),'break')[1])
        shell=ttk.Frame(window,padding=20); shell.pack(fill='both',expand=True)
        heading=ttk.Frame(shell); heading.pack(fill='x')
        self.guide_title=ttk.Label(heading,font=('Segoe UI',18,'bold')); self.guide_title.pack(side='left')
        self.branch_badge=ttk.Label(heading,font=('Segoe UI',10,'bold'),foreground=AMBER); self.branch_badge.pack(side='right')
        self.comparison=ttk.Label(shell,wraplength=780,font=('Segoe UI',11),foreground=MUTED); self.comparison.pack(fill='x',pady=(8,12))
        self.checklist_context=ttk.Label(shell,wraplength=780,font=('Segoe UI',10),foreground=MUTED); self.checklist_context.pack(fill='x',pady=(0,8))
        footer=ttk.Frame(shell,padding=(0,12,0,0)); footer.pack(side='bottom',fill='x')
        self.selected_step=ttk.Label(footer,font=('Segoe UI',10),wraplength=780); self.selected_step.pack(fill='x',pady=(0,8))
        self.action_toggle=ttk.Button(footer,text='Completion and repeat controls',command=lambda:self.note.focus_set())
        self.guide_actions=ttk.Frame(footer); self.guide_actions.pack(fill='x')
        ttk.Label(self.guide_actions,text='Completion note — describe what you actually did').pack(anchor='w')
        row=ttk.Frame(self.guide_actions); row.pack(fill='x',pady=(4,8))
        self.note=ttk.Entry(row); self.note.pack(side='left',fill='x',expand=True,padx=(0,8))
        self.complete_button=ttk.Button(row,text='Record completion',command=self.complete); self.complete_button.pack(side='right')
        self.note.bind('<Return>',lambda _:self.complete())
        row=ttk.Frame(self.guide_actions); row.pack(fill='x')
        self.measurement=ttk.Entry(row,width=8); self.measurement.insert(0,'585'); self.measurement.pack(side='left')
        self.capture_button=ttk.Button(row,text='Repeat ms',command=lambda:self.checklist_call(lambda:self.controller.capture(self.measurement.get()),'Repeat captured. Review updated evidence.'))
        self.capture_button.pack(side='left',padx=8)
        self.followup_button=ttk.Button(row,text='Send follow-up',command=self.send_checklist_followup)
        self.followup_button.pack(side='left')
        ttk.Button(row,text='Close',command=self.close_checklist).pack(side='right')
        steps_shell=ttk.Frame(shell); steps_shell.pack(fill='both',expand=True)
        self.steps_canvas=tk.Canvas(steps_shell,bg=BG,highlightthickness=1,highlightbackground=HUD_BORDER,takefocus=True)
        self.steps_scrollbar=ttk.Scrollbar(steps_shell,orient='vertical',command=self.steps_canvas.yview)
        self.steps_canvas.configure(yscrollcommand=self.steps_scrollbar.set)
        self.steps_canvas.pack(side='left',fill='both',expand=True); self.steps_scrollbar.pack(side='right',fill='y')
        self.steps_frame=ttk.Frame(self.steps_canvas)
        self.steps_window=self.steps_canvas.create_window(0,0,window=self.steps_frame,anchor='nw')
        self.step_widgets=[]; self.step_signature=None
        self.steps_canvas.bind('<Configure>',self.resize_guide)
        self.steps_frame.bind('<Configure>',lambda _:self.steps_canvas.configure(scrollregion=self.steps_canvas.bbox('all')))
        self.steps_canvas.bind('<MouseWheel>',self.scroll_guide)
        for key,amount,unit in (('<Down>',1,'units'),('<Up>',-1,'units'),('<Next>',1,'pages'),('<Prior>',-1,'pages')):
            self.steps_canvas.bind(key,lambda _,n=amount,u=unit:(self.steps_canvas.yview_scroll(n,u),'break')[1])
        shell.bind('<Configure>',lambda e:(self.comparison.configure(wraplength=max(400,e.width-40)),self.checklist_context.configure(wraplength=max(400,e.width-40)),self.selected_step.configure(wraplength=max(400,e.width-40))))

    def open_checklist(self):
        self.pause(); self.refresh_guide()
        self.checklist_binding=self.current_checklist_binding()
        self.checklist_window.title('Received checklist — '+self.controller.selected)
        self.checklist_window.deiconify(); self.checklist_window.lift()
        if self.step_widgets: self.step_widgets[0].focus_set()
        else: self.steps_canvas.focus_set()
        return self.checklist_window

    def current_checklist_binding(self):
        role=self.controller.model()['crew']
        snapshot=role.get('current_snapshot') or {}
        protocol=role.get('protocol') or {}
        evaluation=role.get('evaluation') or {}
        return (self.controller.selected,self.controller.current.incarnation,snapshot.get('digest'),
                (protocol.get('protocol') or {}).get('id'),evaluation.get('outcome'))

    def invalidate_checklist(self):
        self.checklist_window.withdraw(); self.checklist_binding=None
        self.note.delete(0,'end'); self.selected_step_index=None

    def close_checklist(self):
        self.invalidate_checklist(); self.assistant_button.focus_set()

    def checklist_call(self,action,success):
        if self.checklist_binding!=self.current_checklist_binding():
            self.invalidate_checklist()
            self.status.set('Checklist context changed. Reopen the current received steps before recording.')
            return
        self.call(action,success)

    def send_checklist_followup(self):
        context=self.controller.followup_context()
        confirmed=False
        if context['requires_confirmation']:
            confirmed=messagebox.askyesno('Send historical work and changed evidence?',
                context['message']+'\n\nSend this historical context to Earth?',parent=self.checklist_window)
            if not confirmed: return
        self.checklist_call(lambda:self.controller.followup(confirm_historical=confirmed),
            'Follow-up sent: historical work and changed evidence; no current step completed.' if confirmed else
            'Follow-up sent with current completion records.')

    def toggle_actions(self):
        self.action_open=True; self.open_checklist(); self.note.focus_set()

    def build_surgeon(self,parent):
        ttk.Label(parent,text='Earth medical link',style='Card.TLabel',font=('Segoe UI',14,'bold')).pack(anchor='w')
        ttk.Label(parent,text='SIMULATED / UTC / 20 MIN EACH WAY',style='CardMuted.TLabel',font=('Segoe UI',8)).pack(anchor='w',pady=(3,10))
        self.communication=tk.Canvas(parent,width=255,height=160,bg=BG,highlightthickness=0,takefocus=True)
        self.communication.pack(fill='both',expand=True,pady=(6,0)); self.communication.bind('<Configure>',lambda _:self.draw_communication())
        self.communication.bind('<Return>',lambda _:self.toggle_earth_review())
        FocusHelp(self.communication,self.status,'Packet progress represents actual simulated delivery. Earth receives evidence only when a packet arrives.')
        self.link_countdown=ttk.Label(parent,style='Card.TLabel',font=('Segoe UI',11,'bold')); self.link_countdown.pack(anchor='w',pady=(10,6))
        self.earth_summary=ttk.Label(parent,style='CardMuted.TLabel',wraplength=290); self.earth_summary.pack(fill='x',pady=(0,12))
        ttk.Button(parent,text='Read delivered messages',command=self.show_messages).pack(fill='x',pady=(0,6))
        self.send_button=ttk.Button(parent,text='Send consultation',style='Accent.TButton',command=lambda:self.call(self.controller.send_consultation,'Consultation sent to Earth.')); self.send_button.pack(fill='x',pady=4)
        self.advance_button=ttk.Button(parent,text='Advance delivery +20 min',command=lambda:self.call(self.controller.advance,'Delivery clock advanced.')); self.advance_button.pack(fill='x',pady=4)
        FocusHelp(self.send_button,self.status,'Send a persisted snapshot of this crew evidence through the simulated delayed link.')
        FocusHelp(self.advance_button,self.status,'Advance simulated delivery time. This is a demo control, not a live network connection.')
        self.earth_review_toggle=ttk.Button(parent,text='Earth review',command=self.toggle_earth_review,style='Small.TButton'); self.earth_review_toggle.pack(anchor='w',pady=(10,8))
        self.earth_tabs=ttk.Notebook(parent); self.earth_tabs.pack(fill='both',expand=True)
        messages=self.card(self.earth_tabs,4); review=self.card(self.earth_tabs,4); refs=self.card(self.earth_tabs,4)
        for frame,name in ((messages,'Messages'),(review,'Review'),(refs,'References')): self.earth_tabs.add(frame,text=name)
        self.message_canvas=tk.Canvas(messages,width=280,height=110,bg=BG,highlightthickness=0)
        self.message_canvas.pack(fill='both',expand=True); self.message_canvas.bind('<Configure>',lambda _:self.draw_messages()); self.message_canvas.bind('<Button-1>',lambda _:self.show_messages()); self.message_canvas.configure(cursor='hand2')
        ttk.Label(review,text='LAST RECEIVED ON EARTH',style='CardMuted.TLabel',font=('Segoe UI',9)).pack(anchor='w')
        self.earth_reading=ttk.Label(review,style='Card.TLabel',font=('Consolas',20,'bold')); self.earth_reading.pack(anchor='w')
        self.earth_received=ttk.Label(review,style='CardMuted.TLabel',font=('Segoe UI',9)); self.earth_received.pack(anchor='w')
        self.prereq_label=ttk.Label(review,style='CardMuted.TLabel',wraplength=290); self.prereq_label.pack(fill='x',pady=8)
        self.author_button=ttk.Button(review,text='Author Plan A / B',style='Accent.TButton',command=self.author_dialog); self.author_button.pack(fill='x',pady=4)
        ttk.Button(review,text='Inspect received snapshot',command=lambda:self.show_role('doctor')).pack(fill='x',pady=4)
        ttk.Label(refs,text='Separate NASA source cases',style='Card.TLabel',font=('Segoe UI',11,'bold')).pack(anchor='w')
        ttk.Label(refs,text='Not patient matches / units unverified',style='CardMuted.TLabel',font=('Segoe UI',8)).pack(anchor='w',pady=(2,8))
        self.reference_canvas=tk.Canvas(refs,width=280,height=110,bg=BG,highlightthickness=0); self.reference_canvas.pack(fill='both',expand=True); self.reference_canvas.bind('<Configure>',lambda _:self.draw_references())
        ttk.Button(refs,text='Reference details',command=lambda:self.show_text('NASA reference cases',self.reference_result or 'No library loaded.')).pack(fill='x',pady=3)
        self.reference_status=ttk.Label(refs,text='No library loaded',style='CardMuted.TLabel',wraplength=290,font=('Segoe UI',8)); self.reference_status.pack(fill='x',pady=4)
        row=ttk.Frame(refs,style='Card.TFrame'); row.pack(fill='x')
        self.nlsp_button=ttk.Button(row,text='Load library',style='Small.TButton',command=self.load_nlsp); self.nlsp_button.pack(side='left')
        self.fetch_button=ttk.Button(row,text='Fetch NASA',style='Small.TButton',command=self.fetch_references); self.fetch_button.pack(side='right')
        # Reserve controls, then share remaining height between meaningful fields.
        for container,flex in ((parent,self.communication),(messages,self.message_canvas),(refs,self.reference_canvas)):
            container.columnconfigure(0,weight=1)
            children=[(widget,widget.pack_info()) for widget in container.winfo_children()]
            for widget,_ in children: widget.pack_forget()
            for index,(widget,info) in enumerate(children):
                widget.grid(row=index,column=0,sticky='nsew',pady=info.get('pady',0))
                if widget is flex: container.rowconfigure(index,weight=1)
        self.link_graph_row=self.communication.grid_info()['row']; self.review_row=self.earth_tabs.grid_info()['row']
        self.earth_tabs.grid_remove()
        parent.bind('<Configure>',lambda e:self.earth_summary.configure(wraplength=max(220,e.width-24)))

    def build_signals(self):
        self.signal_panels={}
        chooser=ttk.Frame(self.signals,style='Card.TFrame'); chooser.pack(side='left')
        selectors={}
        for channel in ('ECG','BVP'):
            selectors[channel]=ttk.Button(chooser,text=channel,style='Small.TButton',command=lambda c=channel:self.select_channel(c))
            selectors[channel].pack(side='left',padx=(0,4))
        for channel in ('ECG','BVP'):
            area=ttk.Frame(self.signals,style='Card.TFrame')
            caption=ttk.Label(area,style='CardMuted.TLabel',font=('Segoe UI',8)); caption.pack(side='left')
            stamp=ttk.Label(area,style='CardMuted.TLabel',font=('Consolas',8)); stamp.pack(side='right')
            controls=ttk.Frame(self.signals,style='Card.TFrame')
            button=ttk.Button(controls,text='Play',style='Small.TButton',width=7,command=lambda c=channel:self.toggle_signal(c)); button.pack(side='left')
            ttk.Button(controls,text='Reset',style='Small.TButton',command=lambda c=channel:self.reset_signal(c)).pack(side='left',padx=3)
            speed=ttk.Combobox(controls,values=('0.5x','1x','2x'),state='readonly',width=4); speed.set('1x'); speed.pack(side='left'); speed.bind('<<ComboboxSelected>>',lambda _,c=channel:self.change_signal_speed(c))
            position=tk.DoubleVar(master=self.root,value=0); scale=ttk.Scale(controls,variable=position,from_=0,to=60,command=lambda v,c=channel:self.scrub_signal(c,v)); scale.pack(side='left',fill='x',expand=True,padx=5)
            # Only history is displayed. A hidden canvas retains the existing source renderer API.
            chart=tk.Canvas(self.signals,height=75,bg=PLOT,highlightthickness=0)
            self.signal_panels[channel]=dict(chart=chart,area=area,controls=controls,selector=selectors[channel],stamp=stamp,button=button,speed=speed,caption=caption,scale=scale,position=position,record=None,window=None)
            FocusHelp(button,self.status,'Play or pause the selected NASA recording on its independent source clock. It is not live crew telemetry.')
        self.source_status=ttk.Label(self.signals,foreground=MUTED,font=('Segoe UI',8),wraplength=440)
        self.select_channel('ECG')

    def select_channel(self,channel):
        self.selected_channel=channel
        for name,panel in self.signal_panels.items():
            panel['selector'].configure(style='Accent.TButton' if name==channel else 'Small.TButton')
            panel['area'].pack_forget(); panel['controls'].pack_forget()
            if name==channel:
                panel['controls'].pack(side='left',fill='x',expand=True)
        if hasattr(self,'projection'): self.refresh_metric(); self.draw_history('crew')

    def select_vital(self):
        self.selected_vital=('HR','BP','SpO2','temperature','sleep')[self.vital_selector.current()]
        self.select_region('vital')

    def open_monitor(self,crew):
        self.pause(); self.stop_signal(); self.crew_var.set(crew); self.controller.select(crew); self.note.delete(0,'end'); self.selected_step_index=None
        self.invalidate_checklist(); self.action_open=False
        self.home.pack_forget(); self.monitor.pack(fill='both',expand=True); self.screen='monitor'; self.change_source(); self.set_measurements(False); self.set_earth_review(False); self.refresh()
        self.feedback.start(self.draw_condition,reduced_motion=self.reduced_motion)

    def show_home(self):
        self.pause(); self.stop_signal(); self.feedback.cancel(); self.monitor.pack_forget(); self.home.pack(fill='both',expand=True); self.screen='home'; self.refresh()

    def select_crew(self):
        self.open_monitor(self.crew_var.get())

    def start_monitor(self):
        # Recorded source playback is an explicit action, never an ambient cue.
        return

    def toggle_measurements(self):
        self.set_measurements(not self.measurements_open)

    def set_measurements(self,opened):
        self.measurements_open=bool(opened)
        self.feedback.cancel(); self.feedback_progress=1.0
        self.measurements_toggle.configure(text='Back to condition' if opened else 'View measurements')
        self.action_panel.place_forget(); self.action_panel.pack_forget()
        if opened:
            self.monitor_status.pack(side='left',padx=10)
            self.figure_controls.place_forget()
            self.next_action_label.pack_forget()
            self.condition_cue.pack_forget(); self.measurement_mode_label.pack_forget()
            self.action_panel.pack(side='bottom',fill='x',pady=(8,0))
            self.evidence_frame.pack(fill='both',expand=True,pady=(4,0))
            # Full checklist stays in its own workspace.
        else:
            self.monitor_status.pack_forget()
            self.next_action_label.pack(anchor='w',pady=(6,4))
            self.stop_signal(); self.evidence_frame.pack_forget()
            self.measurement_mode_label.pack(anchor='w',pady=(8,0))
            self.condition_cue.pack(fill='both',expand=True,pady=(10,0),before=self.measurement_mode_label)
            self.figure_controls.place(relx=.02,rely=.72,relwidth=.40)
            self.action_panel.place(relx=.46,rely=.62,relwidth=.52,relheight=.34)
            self.action_panel.lift(); self.figure_controls.lift()
            self.draw_condition()
        if hasattr(self,'projection'): self.refresh_guide()

    def focus_region(self,region):
        self.figure_focus=region
        # Do not replace hit items during Enter/Leave: Tk would repick them
        # repeatedly under a stationary pointer. Focus only changes the outline.
        for name in ('head','heart','vital'):
            self.condition_cue.itemconfigure(name,outline=ACCENT if name==region else '')

    def bind_figure(self,canvas):
        def motion(event):
            region=target_at(canvas,event.x,event.y)
            canvas.configure(cursor='hand2' if region else '')
            if canvas is self.condition_cue: self.focus_region(region)
        def click(event):
            region=target_at(canvas,event.x,event.y)
            if region: self.select_region(region)
        canvas.bind('<Motion>',motion)
        canvas.bind('<Button-1>',click)
        canvas.bind('<Leave>',lambda _:(canvas.configure(cursor=''),self.focus_region(None)))

    def toggle_earth_review(self):
        self.set_earth_review(not self.earth_review_open)

    def set_earth_review(self,opened):
        self.earth_review_open=bool(opened)
        self.earth_review_toggle.configure(text='Hide Earth review' if opened else 'Earth review')
        if opened:
            self.communication.grid_remove(); self.earth_summary.grid_remove(); self.earth_tabs.grid()
            self.earth_panel.rowconfigure(self.link_graph_row,weight=0)
            self.earth_panel.rowconfigure(self.review_row,weight=1)
        else:
            self.earth_tabs.grid_remove(); self.communication.grid(); self.earth_summary.grid()
            self.earth_panel.rowconfigure(self.link_graph_row,weight=1)
            self.earth_panel.rowconfigure(self.review_row,weight=0)

    def select_region(self,region):
        self.set_measurements(True)
        self.selected_region=region
        if region=='vital':
            self.vital_selector.current(('HR','BP','SpO2','temperature','sleep').index(self.selected_vital))
        if region=='heart':
            self.signals.grid(); self.nasa_banner.grid(); self.metric_reading.grid_remove(); self.metric_baseline.grid_remove()
        else:
            self.signals.grid_remove(); self.nasa_banner.grid_remove(); self.metric_reading.grid(); self.metric_baseline.grid()
        self.chart_legend.configure(text='Separate source / shaded: author exclusions' if region=='heart' else 'Time (UTC) / ms | Bands: earlier history ±1 / ±2 SD')
        self.refresh_metric(); self.draw_body(); self.draw_history('crew')

    def jump(self,index):
        self.pause(); self.controller.seek(index); self.refresh()

    def reset_guided(self):
        if self.controller.current.mode in ('operator','local') and not messagebox.askyesno('Reset scenario','Replace this workspace with the guided scenario? Saved files are preserved.',parent=self.root): return
        self.pause(); self.stop_signal(); self.controller.reset(); self.selected_step_index=None; self.refresh()
        if self.screen=='monitor': self.start_monitor()

    def new_operator(self):
        if not messagebox.askyesno('New operator session','Start a new operator workspace for this crew? Save the current session first if needed.',parent=self.root): return
        self.pause(); self.stop_signal(); self.controller.reset(interactive=True); self.note.delete(0,'end'); self.selected_step_index=None; self.refresh()
        self.status.set('Operator mode: capture a repeat, send to Earth, then advance delivery to author a guide.')

    def onboard_assistant(self):
        from .assistant_ui import AssistantDialog
        dialog=AssistantDialog(self.root,self.controller,refresh=self.refresh,
            tools={'manual':lambda:self.local_tools(initial_tab='manual'),
                   'signal':lambda:self.local_tools(initial_tab='signal'),
                   'reaction':self.reaction_dialog},
            on_close=lambda d:self.assistant_dialogs.remove(d) if d in self.assistant_dialogs else None)
        self.assistant_dialogs.append(dialog)
        return dialog

    def local_tools(self, *, initial_tab='manual'):
        """Explicit local acquisition; all dialogs are bound to one crew/session."""
        from .local_monitoring import METRICS
        window=tk.Toplevel(self.root); self.local_windows.append(window)
        window.title('Local measurements / '+self.controller.selected); window.geometry('850x700'); window.configure(bg=BG)
        area=ttk.Frame(window,padding=14); area.pack(fill='both',expand=True)
        ttk.Label(area,text='Laptop tools · no external sensors',font=('Segoe UI',15,'bold')).pack(anchor='w')
        ttk.Label(area,text='Record an observation, review a signal, or inspect recorded history. No external sensors connected.',wraplength=800).pack(anchor='w',pady=8)
        bound_crew=self.controller.selected; bound_session=self.controller.current
        def require():
            if self.controller.selected!=bound_crew or self.controller.current is not bound_session:
                raise ValueError('This tool belongs to another crew/session. Close it and reopen Laptop tools.')
            self.controller._require_local()
        def perform(fn,message):
            try: require(); result=fn(); self.refresh(); self.status.set(message); return result
            except (ValueError,OSError,TypeError) as exc: messagebox.showerror('Local action not applied',str(exc),parent=window)
        def begin():
            nonlocal bound_session
            if self.controller.selected!=bound_crew:
                messagebox.showerror('Crew changed','Close and reopen tools for the selected crew.',parent=window); return
            self.pause(); self.controller.start_local(); bound_session=self.controller.current; self.refresh(); update_history()
            title.configure(text='Local recorded session / '+bound_crew)
        top=ttk.Frame(area); top.pack(fill='x')
        ttk.Button(top,text='Start empty local session',command=begin).pack(side='left')
        ttk.Button(top,text='Timed reaction check',command=lambda:perform(self.reaction_dialog,'Reaction check opened.')).pack(side='left',padx=8)
        def export_examples():
            from .local_monitoring import export_import_examples
            directory=filedialog.askdirectory(parent=window,title='Export readable import schema and labelled examples')
            if directory:
                try:
                    paths=export_import_examples(directory,subject_id='LOCAL:crew-'+('001' if bound_crew==CREWS[0] else '002'))
                    self.status.set('Import schema and explicitly labelled examples exported: '+str(Path(paths[0]).parent))
                except (ValueError,OSError) as exc: messagebox.showerror('Examples not exported',str(exc),parent=window)
        # Export action is placed with recorded history below.
        # Model demonstration is placed with signal review below.
        
        title=ttk.Label(area,text='Current mode: '+self.controller.current.mode); title.pack(anchor='w',pady=7)
        notebook=ttk.Notebook(area); notebook.pack(fill='both',expand=True)
        manual=ttk.Frame(notebook,padding=12); signals=ttk.Frame(notebook,padding=12); history=ttk.Frame(notebook,padding=12)
        notebook.add(manual,text='Measure'); notebook.add(signals,text='Signals'); notebook.add(history,text='History')
        ttk.Button(history,text='Export import examples',command=export_examples).pack(side='bottom',pady=5)
        ttk.Button(signals,text='Open separate model demo',command=self.model_demo).pack(side='bottom',pady=5)
        notebook.select(signals if initial_tab=='signal' else manual)
        metrics=[k for k in METRICS if k not in ('reaction_time','signal_review','assistant_review')]
        form={}
        friendly={'metric':'Measurement','value':'Value','unit':'Unit','observed_at':'Observed at (UTC)',
            'source_id':'Source / device','method':'Measurement method','quality':'Evidence quality','note':'Context note'}
        details_form=ttk.Frame(manual)
        def toggle_details():
            if details_form.winfo_manager(): details_form.grid_remove()
            else: details_form.grid(row=5,column=0,columnspan=2,sticky='ew',pady=5)
        for i,(label,default) in enumerate((('metric','heart_rate'),('value',''),('unit','bpm'),
                ('quality','review'),('observed_at',datetime.now(timezone.utc).isoformat()),('source_id','manual-diary'),
                ('method','user-entered instrument reading'),('note',''))):
            container=manual if i<4 else details_form
            row=i if i<4 else i-4
            ttk.Label(container,text=friendly[label]).grid(row=row,column=0,sticky='w',pady=3)
            if label in ('metric','quality'):
                widget=ttk.Combobox(container,state='readonly',values=metrics if label=='metric' else ('review','usable','missing'))
                widget.set(default)
            else: widget=ttk.Entry(container); widget.insert(0,default)
            widget.grid(row=row,column=1,sticky='ew',padx=10,pady=3); form[label]=widget
        manual.columnconfigure(1,weight=1); details_form.columnconfigure(1,weight=1)
        ttk.Button(manual,text='Source, time & context',command=toggle_details).grid(row=4,column=0,columnspan=2,sticky='w',pady=5)
        def metric_changed(_=None):
            form['unit'].delete(0,'end'); form['unit'].insert(0,METRICS[form['metric'].get()][0])
            if form['metric'].get().startswith('sleep_'):
                form['method'].delete(0,'end'); form['method'].insert(0,'self-reported sleep diary v1')
        form['metric'].bind('<<ComboboxSelected>>',metric_changed)
        ttk.Label(manual,text='Quality review is the default. Select usable only to attest the entry and method are suitable.\nBP needs same-time/source/method systolic and diastolic records. Sleep diary is not sleep staging.\nMissing value + missing quality remains unavailable. C = degrees Celsius.',wraplength=740).grid(row=8,column=0,columnspan=2,sticky='w',pady=12)
        def record():
            row={k:w.get() for k,w in form.items()}; row['subject_id']=self.controller.local_subject
            if not messagebox.askokcancel('Review measurement', '\n'.join(friendly[k]+': '+str(row[k]) for k in form),parent=window): return
            result=perform(lambda:self.controller.capture_manual(row),'Operator measurement recorded. Send consultation for review.')
            if result: update_history()
        ttk.Button(manual,text='Record timestamped measurement',command=record).grid(row=9,column=0,columnspan=2,sticky='w')
        def import_rows():
            path=filedialog.askopenfilename(parent=window,title='Import local observations (CSV / JSON)',filetypes=[('Observations','*.csv *.json')])
            if path and perform(lambda:self.controller.import_observations(path),'Recorded observations imported.'): update_history()
        ttk.Button(manual,text='Import observation CSV / JSON',command=import_rows).grid(row=10,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Button(manual,text='Import format help',command=lambda:self.show_text('Observation import format','Requires subject_id='+self.controller.local_subject+', metric, value, unit, observed_at (timezone), source_id, method, quality. JSON is an array of objects; CSV uses these headers. Limit 500 observations / 2 MiB. Timed checks cannot be imported as typed values.')).grid(row=11,column=0,columnspan=2,sticky='w')
        signal_choice=ttk.Combobox(signals,state='readonly'); signal_choice.pack(fill='x')
        signal_canvas=tk.Canvas(signals,height=130,bg=PLOT,highlightthickness=0); signal_canvas.pack(fill='x',pady=8)
        details=scrolledtext.ScrolledText(signals,height=8,bg=PLOT,fg=INK,wrap='word'); details.pack(fill='both',expand=True)
        row=ttk.Frame(signals); row.pack(fill='x',pady=5)
        ttk.Label(row,text='Samples [start, end)').pack(side='left')
        start=ttk.Entry(row,width=8); start.insert(0,'0'); start.pack(side='left',padx=4)
        end=ttk.Entry(row,width=8); end.insert(0,'16'); end.pack(side='left')
        reason=ttk.Entry(signals); reason.insert(0,'Record acquisition issue / repeat reason'); reason.pack(fill='x')
        request_choice=ttk.Combobox(signals,state='readonly'); request_choice.pack(fill='x',pady=5)
        def selected_signal():
            require(); return next(s for s in self.controller.current.local_data['signals'] if s['id']==signal_choice.get())
        def show_signal(_=None):
            try:
                signal=selected_signal(); signal_canvas.delete('all'); values=signal['samples']; good=[v for v in values if finite(v)]
                if good:
                    w=max(signal_canvas.winfo_width(),500); h=130; lo,hi=min(good),max(good); last=None
                    # Bounded decimation for display only; raw arrays remain exact.
                    step=max(1,len(values)//1200)
                    times=signal.get('sample_times_seconds')
                    source_times=times if times is not None else [i/signal['sample_rate_hz'] for i in range(len(values))]
                    first,last_time=source_times[0],source_times[-1]
                    for i in range(0,len(values),step):
                        if any(v is None for v in values[max(0,i-step+1):i+1]): last=None; continue
                        v=values[i]
                        if not finite(v): last=None; continue
                        p=(8+(source_times[i]-first)/max(last_time-first,1e-9)*(w-16),110-(v-lo)/max(hi-lo,1e-9)*90)
                        if last: signal_canvas.create_line(*last,*p,fill=TRACE)
                        last=p
                    signal_canvas.create_text(8,8,text=f'{signal["channel"]} / {signal["unit"]} / independent source clock',anchor='w',fill=MUTED)
                from .signal_review import analyze_local_signal
                try: analysis=analyze_local_signal(signal)
                except ValueError as exc: analysis={'status':'unavailable','reason':str(exc),'action':'Original trace remains reviewable; retain all samples.'}
                evidence={k:v for k,v in signal.items() if k not in ('samples','sample_times_seconds')}
                evidence.update(sample_count=len(values),analysis=analysis,
                    segments=[s for s in self.controller.current.local_data['segments'] if s['signal_id']==signal['id']],
                    repeat_requests=[r for r in self.controller.current.local_data['repeat_requests'] if r['signal_id']==signal['id']])
                text_content(details,json.dumps(evidence,indent=2,ensure_ascii=False))
                request_choice['values']=[r['id'] for r in evidence['repeat_requests'] if r['resolved_at'] is None]
                if request_choice['values']: request_choice.current(0)
            except (ValueError,StopIteration,ImportError,OSError) as exc: text_content(details,str(exc))
        signal_choice.bind('<<ComboboxSelected>>',show_signal)
        def import_sig():
            path=filedialog.askopenfilename(parent=window,title='Import local ECG / BVP JSON',filetypes=[('Signal JSON','*.json')])
            if path:
                signal=perform(lambda:self.controller.add_signal(path),'Signal imported for technical review.')
                if signal:
                    signal_choice['values']=[s['id'] for s in self.controller.current.local_data['signals']]; signal_choice.set(signal['id']); show_signal(); update_history()
        actions=ttk.Frame(signals); actions.pack(fill='x',pady=5)
        ttk.Button(actions,text='Import signal JSON',command=import_sig).pack(side='left')
        def mark():
            perform(lambda:self.controller.mark_segment(selected_signal()['id'],start.get(),end.get(),reason.get()),'Review segment recorded.')
            show_signal(); update_history()
        def request():
            perform(lambda:self.controller.request_repeat(selected_signal()['id'],reason.get()),'Repeat request queued.'); show_signal(); update_history()
        def resolve():
            perform(lambda:self.controller.resolve_repeat(request_choice.get(),reason.get()),'Repeat resolution recorded.'); show_signal(); update_history()
        ttk.Button(actions,text='Mark segment',command=mark).pack(side='left',padx=4)
        ttk.Button(actions,text='Request repeat',command=request).pack(side='left')
        ttk.Button(actions,text='Resolve request',command=resolve).pack(side='left',padx=4)
        ttk.Label(signals,text='Signal JSON: subject_id, channel ECG/BVP, samples (null for gaps), explicit sample_rate_hz or exact sample_times_seconds, unit, observed_at, source_id, method, quality. Max 30k samples / 4 records. Estimates remain source-specific engineering aids.',wraplength=760).pack(anchor='w')
        history_box=scrolledtext.ScrolledText(history,bg=PLOT,fg=INK,wrap='word'); history_box.pack(fill='both',expand=True)
        def update_history():
            if self.controller.current is bound_session:
                text_content(history_box,json.dumps(self.controller.model()['crew']['current_snapshot'],indent=2,ensure_ascii=False))
        update_history()
        if bound_session.local_data:
            signal_choice['values']=[s['id'] for s in bound_session.local_data['signals']]
            if signal_choice['values']: signal_choice.current(0); show_signal()

    def reaction_dialog(self):
        from .reaction_check import ReactionCheck
        try: self.controller._require_local()
        except ValueError as exc: messagebox.showerror('Start a local session',str(exc),parent=self.root); return
        session=self.controller.current; crew=self.controller.selected; task=ReactionCheck()
        window=tk.Toplevel(self.root); self.local_windows.append(window); window.title('Short laptop reaction check'); window.geometry('580x360'); window.configure(bg=BG)
        ttk.Label(window,text='Laptop simple reaction · 5 trials',font=('Segoe UI',17,'bold')).pack(pady=18)
        label=ttk.Label(window,text='Wait for GO, then press Space on this laptop keyboard.\nEarly responses and omissions remain recorded.\nKeep this window focused; no WinSCAT/readiness equivalence.',justify='center'); label.pack(pady=10)
        cue=ttk.Label(window,text='Ready',font=('Segoe UI',28,'bold'),foreground=AMBER); cue.pack(pady=14)
        timer=[None]; focus_job=[None]; active=[True]
        def cancel():
            active[0]=False; task.cancel()
            if timer[0]: self.root.after_cancel(timer[0]); timer[0]=None
            if focus_job[0]: self.root.after_cancel(focus_job[0]); focus_job[0]=None
            if window.winfo_exists(): window.destroy()
            if window in self.local_windows: self.local_windows.remove(window)
        def tick():
            timer[0]=None
            if not active[0] or not window.winfo_exists(): return
            if self.controller.current is not session or self.controller.selected!=crew:
                cancel(); return
            task.tick()
            if task.state=='finished':
                try:
                    self.controller.capture_reaction(task.summary()); self.refresh(); self.status.set('Actual timed laptop check recorded; uncalibrated timing, no clinical readiness claim.')
                    summary=task.summary(); cue.configure(text='Recorded')
                    label.configure(text=f'Median: {summary["value"]} ms / quality: {summary["quality"]}\n{summary["valid_responses"]} valid · {summary["false_starts"]} false starts · {summary["omissions"]} omissions\nTrial details are retained in the recorded snapshot.'); response.configure(state='disabled')
                except (ValueError,OSError) as exc: label.configure(text='Not recorded: '+str(exc))
                active[0]=False; return
            if task.state=='cancelled': cue.configure(text='Cancelled'); return
            if task.ready():
                cue.configure(text='GO',foreground=ACCENT); window.update_idletasks()
                try: task.show_stimulus()
                except ValueError:
                    task.cancel('cue timing limit'); cue.configure(text='Cancelled'); return
            elif task.state=='waiting': cue.configure(text=f'Wait · {len(task.results)+1}/5',foreground=AMBER)
            # A strong self-reference in tick's closure would retain its Tk
            # window after cancellation until a possibly off-thread GC pass.
            timer[0]=self.root.after(10,tick_ref())
        tick_ref=weakref.ref(tick)
        def respond(_=None):
            if active[0] and task.state!='new': task.respond()
            return 'break'
        response=ttk.Button(window,text='Keyboard Space only',state='disabled'); response.pack(pady=8)
        window.bind('<space>',respond)
        def check_focus():
            focus_job[0]=None
            if not active[0] or not window.winfo_exists(): return
            focused=window.focus_displayof()
            if focused is None or focused.winfo_toplevel() is not window: task.cancel('focus lost')
        def focus_out(_):
            if focus_job[0] is None: focus_job[0]=self.root.after_idle(check_focus)
        window.bind('<FocusOut>',focus_out)
        window.protocol('WM_DELETE_WINDOW',cancel)
        self.local_cancels.append(cancel)
        ttk.Button(window,text='Cancel / close',command=cancel).pack()
        task.start(); window.focus_force(); timer[0]=self.root.after(10,tick)

    def model_demo(self):
        from .signal_review import analyze_local_signal
        window=tk.Toplevel(self.root); self.local_windows.append(window); window.title('Trained model / separate NASA-source demonstration'); window.geometry('920x720'); window.configure(bg=BG)
        area=ttk.Frame(window,padding=14); area.pack(fill='both',expand=True)
        ttk.Label(area,text='Trained model · experimental review aid',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(area,text='Controlled injected-corruption benchmark passed; natural NASA author-exclusion benchmark failed.\nActual model scores support manual inspection only. NASA examples retain their own source identity and clocks.\nReview marks/repeat queue in this demo do not enter local personal health histories.',wraplength=870).pack(anchor='w',pady=8)
        controls=ttk.Frame(area); controls.pack(fill='x')
        choices=ttk.Combobox(controls,state='readonly',values=[e['file'] for e in self.examples if e['channel'].upper()=='ECG'],width=24); choices.pack(side='left')
        if choices['values']: choices.current(0)
        chart=tk.Canvas(area,height=160,bg=PLOT,highlightthickness=0); chart.pack(fill='x',pady=8)
        output=scrolledtext.ScrolledText(area,height=14,bg=PLOT,fg=INK,wrap='word'); output.pack(fill='both',expand=True)
        state={'signal':None,'analysis':None,'reviews':[],'repeat_requests':[]}
        def refresh():
            signal=state['signal']
            if not signal: return
            chart.delete('all'); values=signal['samples']; good=[v for v in values if finite(v)]
            if good:
                lo,hi=min(good),max(good); w=max(chart.winfo_width(),600); previous=None; step=max(1,len(values)//1500)
                times=signal['sample_times_seconds']; span=times[-1]-times[0]
                for i in range(0,len(values),step):
                    if any(v is None for v in values[max(0,i-step+1):i+1]): previous=None; continue
                    value=values[i]
                    if not finite(value): previous=None; continue
                    xy=(8+(times[i]-times[0])/max(span,1e-9)*(w-16),140-(value-lo)/max(hi-lo,1e-9)*115)
                    if previous: chart.create_line(*previous,*xy,fill=TRACE)
                    previous=xy
                chart.create_text(8,9,text=signal['subject_id']+' / '+('LABELLED SYNTHETIC CORRUPTION' if signal.get('controlled_injection_demo') else 'ORIGINAL NASA EXCERPT (not clean truth)'),anchor='w',fill=AMBER)
            metadata={k:v for k,v in signal.items() if k not in ('samples','sample_times_seconds')}
            text_content(output,json.dumps({'source':metadata,'analysis':state['analysis'],
                'manual_review_marks':state['reviews'],'repeat_requests':state['repeat_requests']},indent=2,ensure_ascii=False))
        def load():
            try:
                example=next(e for e in self.examples if e['file']==choices.get())
                state.update(signal=model_demo_signal(example),reviews=[],repeat_requests=[])
                state['analysis']=analyze_local_signal(state['signal']); refresh()
            except (StopIteration,ValueError,OSError) as exc: text_content(output,str(exc))
        def corrupt():
            try:
                if state['signal'] is None: load()
                from .signal_review import create_injected_demo
                state['signal']=create_injected_demo(state['signal'],family='hold')
                state['analysis']=analyze_local_signal(state['signal']); state['reviews']=[]; state['repeat_requests']=[]; refresh()
            except (ImportError,ValueError,OSError) as exc: text_content(output,'Demo not applied: '+str(exc))
        ttk.Button(controls,text='Load NASA ECG example',command=load).pack(side='left',padx=6)
        ttk.Button(controls,text='Create labelled corrupted demo',command=corrupt).pack(side='left')
        row=ttk.Frame(area); row.pack(fill='x',pady=7)
        start=ttk.Entry(row,width=8); start.insert(0,'0'); start.pack(side='left')
        end=ttk.Entry(row,width=8); end.insert(0,'2560'); end.pack(side='left',padx=4)
        note=ttk.Entry(row); note.insert(0,'Inspect original trace and acquisition context'); note.pack(side='left',fill='x',expand=True)
        def mark():
            try:
                if state['signal'] is None: raise ValueError('Load a source first.')
                lo,hi=int(start.get()),int(end.get())
                if not 0<=lo<hi<=len(state['signal']['samples']) or not note.get().strip() or len(note.get())>1000 or len(state['reviews'])>=100: raise ValueError('Enter a valid half-open sample range and bounded note (maximum 100 marks).')
                state['reviews'].append({'sample_start':lo,'sample_end_exclusive':hi,'note':note.get(),'reviewed_at':datetime.now(timezone.utc).isoformat()}); refresh()
            except ValueError as exc: messagebox.showerror('Mark not applied',str(exc),parent=window)
        def repeat():
            if state['signal'] is None or not note.get().strip() or len(note.get())>1000 or len(state['repeat_requests'])>=100: return
            state['repeat_requests'].append({'reason':note.get(),'status':'open','requested_at':datetime.now(timezone.utc).isoformat()}); refresh()
        def resolve():
            if not note.get().strip() or len(note.get())>1000: return
            request=next((r for r in state['repeat_requests'] if r['status']=='open'),None)
            if request: request.update(status='resolved',resolution=note.get(),resolved_at=datetime.now(timezone.utc).isoformat()); refresh()
        actions=ttk.Frame(area); actions.pack(fill='x')
        ttk.Button(actions,text='Mark [start, end) samples',command=mark).pack(side='left')
        ttk.Button(actions,text='Queue repeat request',command=repeat).pack(side='left',padx=6)
        ttk.Button(actions,text='Resolve oldest request',command=resolve).pack(side='left')
        def export_report():
            if state['signal'] is None: return
            directory=filedialog.askdirectory(parent=window,title='Export explicit model-demo review report (no raw source arrays)')
            if not directory: return
            from .local_monitoring import safe_path
            import uuid
            try:
                root=safe_path(directory); root.mkdir(parents=True,exist_ok=True)
                target=safe_path(root/('model-demo-review-'+uuid.uuid4().hex[:8]+'.json'))
                metadata={k:v for k,v in state['signal'].items() if k not in ('samples','sample_times_seconds')}
                with target.open('x',encoding='utf-8') as stream: json.dump({'kind':'separate model demo; not personal medical evidence','source':metadata,'analysis':state['analysis'],'reviews':state['reviews'],'repeat_requests':state['repeat_requests']},stream,indent=2)
                self.status.set('Explicit model-demo review report exported: '+str(target))
            except (ValueError,OSError) as exc: messagebox.showerror('Report not exported',str(exc),parent=window)
        ttk.Button(actions,text='Export demo report',command=export_report).pack(side='right')
        text_content(output,'Select Load NASA ECG example. Actual trained model scores and their limitations appear here.')

    def test_alert(self):
        window=tk.Toplevel(self.root); window.title('Test alert · no observation changed'); window.geometry('510x240'); window.configure(bg=PANEL); window.transient(self.root)
        ttk.Label(window,text='TEST ALERT',foreground=RED,background=PANEL,font=('Segoe UI',19,'bold')).pack(anchor='w',padx=24,pady=(24,12))
        ttk.Label(window,text='Monitoring attention test for '+self.controller.selected+'\nThis test does not change observations or health status.',background=PANEL,wraplength=450).pack(anchor='w',padx=24)
        def open_it():
            window.destroy(); self.open_monitor(self.controller.selected)
        ttk.Button(window,text='Open crew monitor',command=open_it).pack(side='left',padx=24,pady=24)
        ttk.Button(window,text='Dismiss test',command=window.destroy).pack(side='right',padx=24,pady=24); self.root.bell()

    def refresh(self):
        for dialog in list(self.assistant_dialogs): dialog.check_context()
        self.model=self.controller.model(); self.projection=mission_projection(self.model); guided=self.controller.current.mode=='guided'
        utc_text=clock(self.controller.current.at)
        self.clock_label.configure(text=utc_text)
        if hasattr(self,'nav_clock'): self.nav_clock.configure(text=utc_text[:5]+' UTC')
        ended=guided and self.consultation_seconds()>=GUIDED_END_SECONDS
        self.play_button.configure(state='normal' if guided and (not ended or self.playing) else 'disabled',text='Pause demo' if self.playing else 'Demo complete' if ended else 'Play demo')
        self.step_button.configure(state='normal' if guided and self.controller.current.stop<len(STOPS)-1 else 'disabled')
        if hasattr(self,'quick_play_button'):
            self.quick_play_button.configure(state='normal' if guided else 'disabled',text='❚❚ Pause' if self.playing else '▶ Play')
        for idx,b in enumerate(self.stage_buttons):
            b.configure(state='normal' if guided else 'disabled',style='StageActive.TButton' if guided and self.controller.current.stop==idx else 'Stage.TButton')
        for idx,b in enumerate(getattr(self,'quick_stage_buttons',())):
            b.configure(state='normal' if guided else 'disabled',style='StageActive.TButton' if guided and self.controller.current.stop==idx else 'Stage.TButton')
        self.consult_scale.configure(state='normal' if guided else 'disabled'); self.setting_scale=True; self.consult_position.set(max(0,min(GUIDED_END_SECONDS,self.consultation_seconds()))); self.setting_scale=False
        self.timeline_summary.configure(text=f'{self.controller.selected} / '+('Guided playback stops at 09:40; Next extended event opens 09:43–10:05 fixture history.' if guided else 'Recorded session; timeline unavailable.'))
        self.quick_demo_button.configure(text='Pause demo' if self.playing else 'Demo complete' if ended else 'Play 09:00–09:40 demo',state='normal' if guided and (not ended or self.playing) else 'disabled')
        self.refresh_cues()
        crew=self.projection['crew']
        self.capture_button.configure(text='Timed check' if self.controller.current.mode=='local' else 'Repeat ms',
            command=self.reaction_dialog if self.controller.current.mode=='local' else lambda:self.checklist_call(lambda:self.controller.capture(self.measurement.get()),'Repeat captured. Review updated evidence.'))
        for key,(name,value,source) in self.sensor_tiles.items():
            sensor=crew['sensors'][key]
            value.configure(text=name+'  '+(str(sensor['value'])+' '+str(sensor['unit']) if sensor['value'] is not None else '—'))
            source.configure(text='Self-entered diary' if key=='sleep' and sensor['value'] is not None else 'Operator recorded' if sensor['value'] is not None else 'Unavailable')
        self.mode_label.configure(text='SYNTHETIC SCENARIO' if guided else 'LOCAL RECORDED SESSION' if self.controller.current.mode=='local' else 'SYNTHETIC OPERATOR SESSION')
        self.measurement_mode_label.configure(text=('Synthetic scenario observations' if guided else 'Laptop-local recorded observations' if self.controller.current.mode=='local' else 'Synthetic operator observations')+' / Available evidence only / not a diagnosis')
        self.guide_actions.pack(fill='x')
        self.refresh_metric(); self.draw_body(); self.draw_history('crew'); self.refresh_guide()
        dm=self.projection['doctor']['metric']; self.earth_reading.configure(text=f'{dm["value"]:g} {dm["unit"]}' if dm['value'] is not None else '— awaiting')
        self.earth_received.configure(text='Observed '+time_label(dm.get('observed_at'))+'\nReceived '+time_label(received_time(self.model['doctor'])) if self.projection['doctor']['digest'] else 'No consultation delivered yet')
        pre=self.controller.prerequisites()
        for key,widget in [('capture',self.capture_button),('send',self.send_button),('author',self.author_button),('complete',self.complete_button),('followup',self.followup_button),('advance',self.advance_button)]: widget.configure(state='normal' if pre[key][0] else 'disabled')
        self.measurement.configure(state='normal' if pre['capture'][0] and self.controller.current.mode!='local' else 'disabled')
        if self.controller.current.mode=='local': self.measurement.pack_forget()
        elif not self.measurement.winfo_manager(): self.measurement.pack(side='left',before=self.capture_button)
        self.prereq_label.configure(text='Guided reviewer actions · use operator mode to author.' if guided else 'Author using received evidence only.' if pre['author'][0] else 'Awaiting consultation. Advance delivery after sending.')

        self.draw_communication(); self.draw_messages(); self.draw_references()

    def refresh_cues(self):
        from .viewmodel import view_model
        from .health_assistant import handoff
        self.cues_refreshed_at=time.monotonic()
        self.roster_projections={}; self.roster_cues={}
        for name,session in self.controller.sessions.items():
            model=view_model(session.workflow.events,session.at)
            projection=mission_projection(model); self.roster_projections[name]=projection
            snapshot=model['crew']['current_snapshot']
            report=handoff(snapshot,crew=name,session_id=session.session_id,now=datetime.now(timezone.utc).isoformat())
            report.update(role='crew',snapshot_digest=snapshot['digest'] if snapshot else None)
            self.roster_cues[name]=project_crew_status(projection['crew'],report,crew=name,session_id=session.session_id,snapshot=snapshot)
            self.draw_roster(name)
        # The controller's validated handoff supplies the selected role summary.
        self.crew_cue=project_crew_status(self.projection['crew'],self.controller.assistant_handoff('crew'),
            crew=self.controller.selected,session_id=self.controller.current.session_id,snapshot=self.model['crew']['current_snapshot'])
        cue=self.crew_cue
        self.monitor_status.configure(text={'red':'URGENT REPORT','amber':'REVIEW NEEDED','neutral':'MORE EVIDENCE NEEDED','green':'AVAILABLE OBSERVATIONS'}[cue['tone']],foreground=self.cue_color(cue))
        self.next_action_label.configure(text=cue['action'],foreground=self.cue_color(cue))
        self.assistant_button.configure(text='Get onboard help now' if cue['tone']=='red' else 'Review received steps' if cue['target']=='checklist' and self.projection['crew']['checklist']['steps'] else 'Open onboard assistant')
        signature=(self.controller.selected,cue['tone'],cue['title'],self.projection['crew']['checklist']['completion_count'],self.projection['crew']['checklist']['original_reply'])
        changed=self.cue_signature is not None and signature!=self.cue_signature
        self.cue_signature=signature
        self.feedback_progress=1.0
        self.draw_condition()  # Urgent words and symbol are painted immediately.
        if changed and self.screen=='monitor' and not self.measurements_open:
            self.feedback.start(self.draw_condition,reduced_motion=self.reduced_motion)

    def next_action(self):
        if self.crew_cue['target']=='checklist' and self.step_widgets:
            self.open_checklist()
            return
        dialog=self.onboard_assistant()
        if self.crew_cue['tone']=='red': dialog.urgent_help()

    def refresh_metric(self):
        if not hasattr(self,'projection'): return
        metric=self.projection['crew']['metric']
        if self.selected_region=='vital':
            sensor=self.projection['crew']['sensors'][self.selected_vital]
            self.metric_name.configure(text=self.vital_selector.get().upper()); self.metric_badge.configure(text=str(sensor.get('quality','missing')).upper())
            self.metric_value.configure(text=str(sensor['value'])+' '+str(sensor['unit']) if sensor['value'] is not None else 'Unavailable',foreground=INK)
            self.metric_change.configure(text=clock(sensor['observed_at']) if sensor.get('observed_at') else 'No observation time')
            self.metric_baseline.configure(text=(str(sensor.get('source_id') or 'No source')+' / '+str(sensor.get('reason') or 'No measurement connected or recorded')))
            self.chart_legend.configure(text='Latest available measurement · no inferred values'); return
        if self.selected_region=='heart':
            record=self.signal_panels[self.selected_channel]['record']
            identity=record.example['file'] if record else self.selected_channel+' recording unavailable'
            self.nasa_banner.configure(text='NASA reference recording — separate from this crew’s observations\n'+identity+' · amplitude units unverified · not sent as crew evidence')
            self.metric_name.configure(text='CARDIOVASCULAR RECORDING'); self.metric_badge.configure(text='NASA SOTERIA RECORDING'); self.metric_value.configure(text=self.selected_channel,foreground=TRACE); self.metric_change.configure(text='Source replay',foreground=MUTED); self.metric_baseline.configure(text='Crew vitals unavailable from this replay / independent source clock'); return
        self.metric_name.configure(text='COGNITIVE RESPONSE'); self.metric_badge.configure(text='LAPTOP CHECK' if self.controller.current.mode=='local' else 'SYNTHETIC MISSION')
        self.metric_value.configure(text=f'{metric["value"]:g} {metric["unit"]}' if metric['value'] is not None else '—',foreground=AMBER if self.projection['crew']['attention'] else INK)
        ref=metric['reference']
        if ref.get('status')=='available':
            self.metric_change.configure(text=f'{ref["zscore"]:+.2f} SD',foreground=AMBER if abs(ref['zscore'])>=2 else TRACE)
            self.metric_baseline.configure(text=reference_label(metric))
        else:
            self.metric_change.configure(text='Baseline unavailable',foreground=MUTED); self.metric_baseline.configure(text=ref.get('reason') or 'Insufficient comparable history')

    def draw_body(self):
        c=self.body; c.delete('all')
        w,h=max(c.winfo_width(),120),max(c.winfo_height(),30)
        height=min(188,max(64,h-30))
        self.body_targets=draw_crew(c,w/2,6,height,active=self.selected_region,images=self.crew_images)
        c.create_text(w/2,min(h-6,height+22),text='Select a category',fill=MUTED,font=('Segoe UI',7))

    def draw_history(self,role):
        if not hasattr(self,'projection'): return
        if self.selected_region=='vital':
            self.history.delete('all')
            self.history.create_text(max(200,self.history.winfo_width())/2,max(100,self.history.winfo_height())/2,
                text='No comparable time history available.\nUse Measure to record or import an observation.',fill=MUTED,font=('Segoe UI',11),justify='center'); return
        if self.selected_region=='heart':
            self.draw_cardiac_focus()
            return
        self.plot_history(self.history,self.projection[role]['metric'])

    def draw_cardiac_focus(self):
        c=self.history; c.delete('all'); w,h=max(c.winfo_width(),200),max(c.winfo_height(),48)
        record=self.signal_panels[self.selected_channel]['record']
        if record is None:
            c.create_text(w/2,h/2,text=self.selected_channel+' recording unavailable',fill=MUTED); return
        result=record.window(); values=[p['value'] for p in result['points'] if p['value'] is not None]
        if not values: return
        low,high=min(values),max(values); margin=max((high-low)*.08,.001); low-=margin; high+=margin
        start,end=result['source_start_seconds'],result['source_end_seconds']
        # The gutter fits signed source amplitudes, including four-digit values.
        amplitude=lambda value:f'{value:.2f}' if abs(value)<1000 else f'{value:.0f}'
        widest=max(len(amplitude(value)) for value in (low,(low+high)/2,high))
        left,right,top,bottom=max(54,8*widest+14),w-12,26,h-30
        x=lambda t:left+(t-start)/max(end-start,.001)*(right-left)
        y=lambda v:bottom-(v-low)/(high-low)*(bottom-top)
        for overlay in result['author_exclusions']:
            lo,hi=max(start,overlay['start']),min(end,overlay['end'])
            if lo<=hi: c.create_rectangle(x(lo),top,x(hi),bottom,fill=EXCLUDED,outline='')
        for fraction in (0,.5,1):
            value=low+(high-low)*fraction
            c.create_line(left,y(value),right,y(value),fill=GRID)
            c.create_text(left-6,y(value),text=amplitude(value),anchor='e',fill=MUTED,font=('Consolas',8))
            seconds=start+(end-start)*fraction
            c.create_text(x(seconds),bottom+13,text=f'{seconds:.1f}s',anchor='w' if fraction==0 else 'e' if fraction==1 else 'center',fill=MUTED,font=('Consolas',8))
        segment=[]
        for point in result['points']:
            if point['value'] is None:
                if len(segment)>=4: c.create_line(*segment,fill=TRACE,width=1.4)
                segment=[]
            else: segment += [x(point['source_seconds']),y(point['value'])]
        if len(segment)>=4: c.create_line(*segment,fill=TRACE,width=1.4)
        c.create_line(x(result['cursor']),top,x(result['cursor']),bottom,fill=INK)
        c.create_text(left,10,text='NASA '+record.example['source_key'].split('/')[0]+' / amplitude units unverified',anchor='w',fill=MUTED,font=('Segoe UI',8))
        c.create_text(right,10,text='Auto scale',anchor='e',fill=MUTED,font=('Segoe UI',8))

    def plot_history(self,canvas,metric):
        canvas.delete('all'); w,h=max(canvas.winfo_width(),220),max(canvas.winfo_height(),48)
        points=metric['history']
        usable=[p for p in points if finite(p.get('value')) and p.get('quality')=='usable']
        if not usable:
            canvas.create_text(w/2,h/2,text='Awaiting observations',fill=MUTED); return
        vals=[p['value'] for p in usable]; ref=metric['reference']; band=ref.get('reference_interval') if ref.get('status')=='available' else None
        ext=vals+(band or []); low,high=min(ext)-15,max(ext)+15; x0,x1,y0,y1=38,w-15,16,h-23
        y=lambda v:y1-(v-low)/max(high-low,1)*(y1-y0)
        if band:
            canvas.create_rectangle(x0,y(band[1]),x1,y(band[0]),fill=BAND_OUTER,outline='')
            canvas.create_rectangle(x0,y(ref['mean']+ref['sd']),x1,y(ref['mean']-ref['sd']),fill=BAND_INNER,outline='')
            canvas.create_line(x0,y(ref['mean']),x1,y(ref['mean']),fill=BASELINE,dash=(3,3))
            canvas.create_text(x1,7,text='Current past-history ±1 / ±2 SD',fill=MUTED,anchor='e',font=('Segoe UI',7))
        for v in (low,(low+high)/2,high):
            canvas.create_line(x0,y(v),x1,y(v),fill=GRID); canvas.create_text(x0-5,y(v),text=f'{v:.0f}',anchor='e',fill=MUTED,font=('Consolas',7))
        times=[datetime.fromisoformat(p['observed_at']).timestamp() for p in points]; start,end=min(times),max(times)
        x=lambda t:x0+(t-start)/max(end-start,1)*(x1-x0); coords=[]; previous=None
        duplicate_times={t for t in times if times.count(t)>1}
        for point,t in zip(points,times):
            if not finite(point.get('value')) or point.get('quality')!='usable' or t in duplicate_times:
                previous=None; canvas.create_text(x(t),y1-4,text='×',fill=AMBER,font=('Segoe UI',8)); continue
            px,py=x(t),y(point['value']); coords.append((px,py))
            if previous:
                ax,ay=previous; canvas.create_line(ax,ay,px,ay,px,py,fill=TRACE,width=2)
            previous=(px,py)
        for px,py in coords: canvas.create_oval(px-2,py-2,px+2,py+2,fill=TRACE,outline='')
        if coords:
            px,py=coords[-1]; color=AMBER if ref.get('zscore') is not None and abs(ref['zscore'])>=2 else TRACE
            canvas.create_oval(px-4,py-4,px+4,py+4,fill=color,outline=PLOT)
        canvas.create_text(x0,h-9,text=clock(points[0]['observed_at'])[:5],anchor='w',fill=MUTED,font=('Consolas',7))
        canvas.create_text(x1,h-9,text=clock(points[-1]['observed_at'])[:5],anchor='e',fill=MUTED,font=('Consolas',7))

    @staticmethod
    def cue_color(cue):
        return {'red':RED,'amber':AMBER,'green':GREEN,'neutral':MUTED}[cue['tone']]

    def _draw_mini_baseline(self, c, x0, y0, x1, y1, metric, ref, *, highlight_color=TRACE, show_labels=True):
        """Render Layer #1 reaction-time trajectory against ±1σ/±2σ reference bands."""
        c.create_rectangle(x0, y0, x1, y1, fill=PLOT, outline=HUD_BORDER, width=1)
        points = metric.get('history') or []
        usable = [p for p in points if finite(p.get('value')) and p.get('quality') == 'usable']
        if not usable:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='Awaiting observations', fill=MUTED, font=('Segoe UI', 8))
            return
        vals = [p['value'] for p in usable]
        band = ref.get('reference_interval') if ref.get('status') == 'available' else None
        ext = vals + (band or [])
        low, high = min(ext) - 12, max(ext) + 12
        px0, px1 = x0 + 6, x1 - 6
        py0, py1 = y0 + 7, y1 - 7
        span = max(high - low, 1)
        y_map = lambda v: py1 - (v - low) / span * max(py1 - py0, 1)
        if band:
            c.create_rectangle(px0, y_map(band[1]), px1, y_map(band[0]), fill=BAND_OUTER, outline='')
            c.create_rectangle(px0, y_map(ref['mean'] + ref['sd']), px1, y_map(ref['mean'] - ref['sd']), fill=BAND_INNER, outline='')
            c.create_line(px0, y_map(ref['mean']), px1, y_map(ref['mean']), fill=BASELINE, dash=(3, 3))
            if show_labels:
                c.create_text(px1 - 1, py0 - 1, text='±2σ', fill=MUTED, anchor='ne', font=('Segoe UI', 6))
        else:
            c.create_line(px0, (py0 + py1) / 2, px1, (py0 + py1) / 2, fill=GRID)
        n = len(usable)
        coords = []
        for idx, pt in enumerate(usable):
            px = px0 + (idx / max(n - 1, 1)) * max(px1 - px0, 1) if n > 1 else (px0 + px1) / 2
            py = y_map(pt['value'])
            coords.append((px, py))
        if len(coords) >= 2:
            flat = []
            for idx, (px, py) in enumerate(coords):
                if idx > 0:
                    flat.extend((px, coords[idx - 1][1]))
                flat.extend((px, py))
            c.create_line(*flat, fill=TRACE, width=1.6)
        for px, py in coords[:-1]:
            c.create_oval(px - 1.8, py - 1.8, px + 1.8, py + 1.8, fill=TRACE, outline='')
        lx, ly = coords[-1]
        dot_color = highlight_color if (ref.get('zscore') is not None and abs(ref['zscore']) >= 2) else TRACE
        c.create_oval(lx - 3.2, ly - 3.2, lx + 3.2, ly + 3.2, fill=dot_color, outline=PLOT, width=1)

    def draw_condition(self,progress=None):
        if not hasattr(self,'condition_cue') or not hasattr(self,'crew_cue'): return
        if progress is not None: self.feedback_progress=progress
        c=self.condition_cue; c.delete('all'); w=max(c.winfo_width(),580); h=max(c.winfo_height(),370)
        cue=self.crew_cue; color=self.cue_color(cue); metric=self.projection['crew']['metric']
        offset=5*(1-self.feedback_progress)
        self.figure_targets=draw_crew(c,w*.22+offset,h*.18,min(h*.48,250),active=self.figure_focus,images=self.crew_images)
        c.create_text(12,6,text=self.controller.selected.upper(),fill=INK,font=('Segoe UI',20,'bold'),anchor='nw')
        mode='Synthetic scenario' if self.controller.current.mode=='guided' else 'Local recorded session' if self.controller.current.mode=='local' else 'Synthetic operator session'
        c.create_text(12,50,text=mode,fill=TRACE,font=('Segoe UI',10),anchor='nw')
        x=w*.46; tw=w-x-14
        c.create_text(x,6,text='REACTION TIME',fill=MUTED,font=('Segoe UI',11,'bold'),anchor='nw')
        c.create_text(x,30,text=measurement_label(metric),fill=INK,font=('Consolas',32,'bold'),anchor='nw',tags=('main-measurement',))
        c.create_text(x,82,text='Observed '+time_label(metric.get('observed_at')),fill=MUTED,font=('Segoe UI',11),anchor='nw')
        ref=metric['reference']
        change=f'{ref["zscore"]:+.2f} SD · n={ref["count"]} earlier observations' if ref.get('status')=='available' else 'Reference unavailable'
        c.create_text(x,109,text=change,fill=TRACE,font=('Segoe UI',11),anchor='nw',width=tw)
        c.create_text(x,140,text=cue['symbol']+'  '+cue['title'],fill=color,font=('Segoe UI',17,'bold'),anchor='nw',width=tw,tags=('condition-title',))
        bounds=c.bbox('condition-title')
        c.create_text(x,(bounds[3] if bounds else 180)+8,text=cue['detail'],fill=MUTED,font=('Segoe UI',11),anchor='nw',width=tw,tags=('condition-scope',))

    def draw_roster(self,crew):
        if not hasattr(self,'roster_projections') or crew not in self.roster_projections: return
        card=self.crew_cards[crew]; cue=self.roster_cues[crew]; color=self.cue_color(cue)
        role=self.roster_projections[crew]['crew']; metric=role['metric']; ref=metric['reference']
        card['state'].configure(text=cue['symbol']+'  '+cue['title'].upper(),foreground=color)
        c=card['chart']; c.delete('all'); w=max(c.winfo_width(),390); h=max(c.winfo_height(),280)
        c.create_rectangle(2,2,w-3,h-3,fill=HUD_SURFACE,outline=HUD_BORDER)
        c.create_rectangle(2,2,w-3,5,fill=color,outline='')
        draw_crew(c,w*.80,h*.20,min(h*.53,230),interactive=False,images=self.crew_images)
        c.create_text(20,18,text=crew.upper(),fill=INK,font=('Segoe UI',23,'bold'),anchor='nw',tags=('crew-id',))
        mode=self.controller.sessions[crew].mode
        source='Synthetic scenario' if mode=='guided' else 'Local recorded session' if mode=='local' else 'Synthetic operator session'
        c.create_text(20,70,text=source,fill=TRACE,font=('Segoe UI',11),anchor='nw',tags=('source-label',))
        c.create_text(20,104,text='Reaction time',fill=MUTED,font=('Segoe UI',11,'bold'),anchor='nw',tags=('roster-metric-name',))
        c.create_text(20,129,text=measurement_label(metric),fill=INK,font=('Consolas',32,'bold'),anchor='nw',tags=('roster-measurement',))
        c.create_text(20,188,text='Observed '+time_label(metric.get('observed_at')),fill=MUTED,font=('Segoe UI',11),anchor='nw')
        change=f'{ref["zscore"]:+.2f} SD · n={ref["count"]} earlier' if ref.get('status')=='available' else 'Reference unavailable'
        c.create_text(20,218,text=change,fill=TRACE,font=('Segoe UI',11),anchor='nw',width=w*.58)
        c.create_text(20,252,text='Descriptive history, not a health range',fill=MUTED,font=('Segoe UI',10),anchor='nw',width=w*.57)
        c.create_text(20,max(305,h-91),text=cue['action'],fill=INK,font=('Segoe UI',13,'bold'),anchor='nw',width=w*.60)
        card['value'].configure(text=cue['title']); card['detail'].configure(text=cue['detail']); card['note'].configure(text=cue['action'])

    def refresh_guide(self):
        if self.checklist_binding is not None and self.checklist_binding!=self.current_checklist_binding(): self.invalidate_checklist()
        role=self.projection['crew']; guide=role['checklist']; steps=guide['steps']; branch=guide['branch']
        self.guide_title.configure(text=f'PLAN {branch} / received checklist' if branch else 'No checklist received',foreground=AMBER if branch=='B' else INK)
        self.branch_badge.configure(text=f'{guide["completion_count"]}/{guide["total_steps"]} recorded' if steps else '')
        self.completion_label.configure(text=f'{guide["completion_count"]}/{guide["total_steps"]} steps recorded'+(' · fixture' if self.controller.current.mode=='guided' else '') if steps else 'No checklist received yet')
        if steps and getattr(self,'crew_cue',{}).get('target')!='checklist':
            self.checklist_button.configure(state='normal'); self.checklist_button.pack(anchor='w',pady=(5,0))
        else: self.checklist_button.pack_forget()
        reviewed,metric=role.get('reviewed'),role['metric']
        if reviewed and reviewed.get('value') is not None:
            self.comparison.configure(text=f'Reviewer used {measurement_label(reviewed)} observed {time_label(reviewed.get("observed_at"))}. Crew now: {measurement_label(metric).lower()} observed {time_label(metric.get("observed_at"))}.')
        else: self.comparison.configure(text='No reviewed measurement is available. Current evidence is not assumed to have reached Earth.')
        guided=self.controller.current.mode=='guided'
        self.checklist_context.configure(text='Synthetic guided fixture. Recording controls are disabled; Next extended event shows explicit fixture completions after 09:40.' if guided else 'Received measurement steps. Select a step, perform it, then explicitly record a completion note. Sending follow-up retains the existing delivery delay.')
        signature=(tuple(steps),tuple(guide['completed_steps']),self.controller.current.mode)
        if signature!=self.step_signature:
            for widget in self.steps_frame.winfo_children(): widget.destroy()
            self.step_widgets=[]
            for i,text in enumerate(steps):
                done=i in guide['completed_steps']
                button=tk.Button(self.steps_frame,text=f'{"Recorded" if done else str(i+1)+"."}  {text}',anchor='w',justify='left',wraplength=max(150,self.steps_canvas.winfo_width()-48) if self.steps_canvas.winfo_width()>1 else 740,bg=RAISED,fg=TRACE if done else INK,activebackground=HOVER,activeforeground=INK,highlightbackground=BORDER,highlightcolor=ACCENT,relief='flat',bd=1,padx=14,pady=12,font=('Segoe UI',12),command=lambda n=i:self.select_step(n),takefocus=True)
                button.pack(fill='x',pady=4,padx=4); self.step_widgets.append(button)
                button.bind('<MouseWheel>',self.scroll_guide)
                button.bind('<FocusIn>',lambda _,n=i:self.reveal_step(n))
                for key,offset in (('<Down>',1),('<Up>',-1)):
                    button.bind(key,lambda _,n=i,d=offset:self.focus_step(n+d))
            if not steps:
                ttk.Label(self.steps_frame,text='Use the onboard assistant while awaiting received steps.',wraplength=600,foreground=MUTED,font=('Segoe UI',12)).pack(anchor='w',padx=12,pady=12)
            self.step_signature=signature; self.steps_canvas.yview_moveto(0)
        if self.selected_step_index is not None and self.selected_step_index>=len(steps): self.selected_step_index=None
        for i,button in enumerate(self.step_widgets): button.configure(bg=SELECTED if i==self.selected_step_index else RAISED)
        self.selected_step.configure(text='Prior completions retained; this reading requires renewed review.' if guide['stale_completions'] else f'Step {self.selected_step_index+1} selected. Enter a completion note.' if self.selected_step_index is not None else 'Select a numbered step to record its completion.')

    def resize_guide(self,event):
        self.steps_canvas.itemconfigure(self.steps_window,width=event.width)
        for button in self.step_widgets: button.configure(wraplength=max(150,event.width-48))

    def scroll_guide(self,event):
        self.steps_canvas.yview_scroll(-1 if event.delta>0 else 1,'units')
        return 'break'

    def reveal_step(self,index):
        if index<len(self.step_widgets):
            widget=self.step_widgets[index]
            top=widget.winfo_y(); bottom=top+widget.winfo_height()
            visible=self.steps_canvas.canvasy(0); height=self.steps_canvas.winfo_height()
            target=top if top<visible else bottom-height if bottom>visible+height else None
            if target is not None: self.steps_canvas.yview_moveto(max(0,target)/max(1,self.steps_frame.winfo_height()))

    def focus_step(self,index):
        if self.step_widgets:
            self.step_widgets[max(0,min(index,len(self.step_widgets)-1))].focus_set()
        return 'break'

    def select_step(self,index):
        self.selected_step_index=index
        for i,b in enumerate(self.step_widgets): b.configure(bg=SELECTED if i==index else RAISED)
        if self.checklist_window.state()=='withdrawn': self.open_checklist()
        self.selected_step.configure(text=f'Step {index+1} selected. Enter a completion note.')
        if self.controller.current.mode in ('operator','local'): self.note.focus_set()
        self.status.set(f'Step {index+1} selected. Enter your completion note.')

    def complete(self):
        if self.selected_step_index is None:
            messagebox.showinfo('Select a step','Select a numbered guide step and enter a completion note.',parent=self.root); return
        self.checklist_call(lambda:self.controller.complete(self.selected_step_index,self.note.get()),'Explicit completion recorded. A follow-up can now be sent.')

    def draw_communication(self):
        if not hasattr(self,'model'): return
        c=self.communication; c.delete('all'); w=max(c.winfo_width(),280); h=max(c.winfo_height(),185)
        for index,(label,role) in enumerate((('Crew now',self.projection['crew']),('Earth last received',self.projection['doctor']))):
            x=12+index*w/2; metric=role['metric']; width=w/2-20
            c.create_text(x,6,text=label,anchor='nw',fill=TRACE if index==0 else INK,font=('Segoe UI',10,'bold'),width=width)
            c.create_text(x,31,text=measurement_label(metric),anchor='nw',fill=INK,font=('Consolas',20 if metric.get('value') is not None else 14,'bold'),width=width)
            c.create_text(x,67,text='Observed '+time_label(metric.get('observed_at')).replace(' UTC',''),anchor='nw',fill=MUTED,font=('Segoe UI',10),width=width)
            received=received_time(self.model['doctor']) if index else None
            c.create_text(x,88,text=('Received '+time_label(received).replace(' UTC','')) if index else 'Onboard · UTC',anchor='nw',fill=MUTED,font=('Segoe UI',10),width=width)
            c.create_text(x,110,text=evidence_age(metric.get('observed_at'),self.model['at']),anchor='nw',fill=MUTED,font=('Segoe UI',10),width=width)
        now=datetime.fromisoformat(self.controller.current.at).timestamp()
        packets=[p for p in self.model['narrator_timeline'] if p['kind']=='packet'][-2:]
        arc_height=max(45,h-144)
        if not packets: draw_path(c,w,arc_height,offset_y=140)
        for i,packet in enumerate(packets):
            sent,arrives=[datetime.fromisoformat(packet[k]).timestamp() for k in ('at','arrives_at')]
            fraction=packet_progress(sent,arrives,now)
            draw_path(c,w,arc_height,reverse=packet['sender']!='crew',lane=i,fraction=fraction,color=TRACE if fraction==1 else ACCENT,offset_y=140)
        seconds=self.projection['communication'].get('seconds_until_arrival')
        countdown=f'Next arrival in {int(seconds)//60:02d}:{int(seconds)%60:02d}' if seconds is not None else 'No delivery pending'
        self.link_countdown.configure(text=countdown); self.home_link.configure(text=countdown+' / 20 min one-way')
        delivered=bool(self.projection['doctor']['digest']); reply=bool(self.projection['crew']['checklist']['original_reply'])
        self.earth_summary.configure(text='Received checklist ready. Open the next step.' if reply else 'Earth has older delivered evidence.' if delivered else 'No consultation delivered to Earth yet.')

    def draw_messages(self):
        if not hasattr(self,'model'): return
        c=self.message_canvas; c.delete('all'); w=max(c.winfo_width(),280); packets=[p for p in self.model['narrator_timeline'] if p['kind']=='packet'][-2:]
        if not packets: c.create_text(8,22,text='No messages sent. Capture and send a consultation.',anchor='w',fill=MUTED,font=('Segoe UI',8))
        for i,p in enumerate(packets):
            y=i*32; c.create_line(0,y+30,w,y+30,fill=GRID); direction='Crew → Earth' if p['sender']=='crew' else 'Earth → Crew'
            c.create_text(8,y+9,text=direction+'  ·  '+p['status'],anchor='w',fill=TRACE if p['status']=='arrived' else AMBER,font=('Segoe UI',8,'bold'))
            c.create_text(8,y+22,text=f'{clock(p["at"])[:5]} sent / {clock(p["arrives_at"])[:5]} arrival · {p["packet_bytes"]:,} B',anchor='w',fill=MUTED,font=('Segoe UI',7))

        reply=self.projection['crew']['checklist'].get('original_reply')
        if reply:
            y=64
            c.create_line(0,y+36,w,y+36,fill=GRID)
            c.create_text(8,y+8,text='RECEIVED REPLY / click to read',anchor='w',fill=TRACE,font=('Segoe UI',8,'bold'))
            c.create_text(8,y+23,text=reply[:max(25,int((w-16)/5.2))]+('...' if len(reply)>max(25,int((w-16)/5.2)) else ''),anchor='w',fill=INK,font=('Segoe UI',8))

    def show_messages(self):
        window=tk.Toplevel(self.root); window.title('Earth medical link / '+self.controller.selected)
        window.geometry('680x470'); window.configure(bg=PANEL); window.transient(self.root)
        ttk.Label(window,text='EARTH MEDICAL LINK',background=PANEL,foreground=TRACE,font=('Segoe UI',16,'bold')).pack(anchor='w',padx=22,pady=(20,4))
        ttk.Label(window,text=self.controller.selected+' / delivered messages',background=PANEL,foreground=MUTED).pack(anchor='w',padx=22,pady=(0,12))
        box=scrolledtext.ScrolledText(window,wrap='word',font=('Segoe UI',11),bg=PLOT,fg=INK,insertbackground=INK,padx=18,pady=16,relief='flat',borderwidth=0,spacing3=8)
        box.pack(fill='both',expand=True,padx=20)
        box.tag_configure('heading',foreground=TRACE,font=('Segoe UI',10,'bold'))
        box.tag_configure('time',foreground=MUTED,font=('Segoe UI',9))
        messages=[]
        for role in ('crew','doctor'):
            for item in self.model[role]['inbox']:
                messages.append((item['received_at'],role,item))
        for _,role,item in sorted(messages,key=lambda item:datetime.fromisoformat(item[0])):
            packet=item['packet']
            box.insert('end',('Flight Surgeon to '+self.controller.selected if role=='crew' else self.controller.selected+' to Flight Surgeon')+'\n','heading')
            box.insert('end',f'Received {clock(item["received_at"])} / {item["packet_bytes"]:,} bytes\n','time')
            if packet.get('type')=='protocol': content=packet['protocol']['original_reply']
            elif packet.get('type')=='snapshot': content='Consultation snapshot received. Open Snapshot to inspect the delivered evidence.'
            else: content='Follow-up received with explicit completion records and updated evidence.'
            box.insert('end',content+'\n\n')
        if not messages: box.insert('end','No messages have arrived yet.')
        box.configure(state='disabled')
        ttk.Button(window,text='Close messages',command=window.destroy).pack(anchor='e',padx=20,pady=12)

    def about(self):
        self.show_text('About BioTwin Sync / data sources',f'''Astronaut Health Monitoring · {__version__}

Offline prototype with independent anonymous synthetic and laptop-local recorded sessions. Laptop tools provide an uncalibrated timed keyboard reaction check, manual measurements/sleep diary and bounded observation/signal imports. A descriptive deviation is a review flag, not a diagnosis. Reviewer-authored measurement guidance is evaluated against current delivered evidence; operator completion is explicit.

NASA SOTERIA waveform recordings are exact source excerpts from terrestrial simulator pilots, separate from the synthetic crew. Independent source clocks; amplitude units and synchronization unverified. Shaded waveform regions mark author heartbeat-detector exclusions, not organ disease. Missing sensors remain unconnected.

NASA NLSP / UTMB Campaign 3: the local optional WinSCAT library parses 26 CSV files / 272 records. Separate BEDREST0543 schema inspection covers 15 CSV files / 2,425 rows. The NASA catalog lists MR085G and BEDREST0543. These are historical ground bed-rest research observations, not emergency training cases. Raw NLSP data are loaded or fetched separately; bundled SOTERIA excerpts are distinct. The 09:00–09:40 consultation and 420–585 ms readings are explicitly synthetic scenario fixtures.

NASA Langley Research Center / NASA System-Wide Safety Project; Fettrow et al. (2025), Scientific Data 12, 1247.
https://doi.org/10.1038/s41597-025-05336-7
CC BY 4.0: https://creativecommons.org/licenses/by/4.0/
No NASA logo or endorsement. Exact source details are accessible below.

WinSCAT reference cases are separate NASA cases, not matched patients. Units/source-local times remain unverified. Personal SD bands require at least five earlier compatible observations. Raw NLSP records are not bundled.

No live hardware, pulse/HRV inference, SpO2, sleep staging, organ health, treatment or task clearance. Local hashes verify byte integrity, not authentication/encryption.

Inactive failed v1 signal-quality experiment: held-out TP 4 / FN 11 / FP 143 / TN 62. Balanced accuracy 28.5%, recall 26.7%, precision 2.7%. Does not drive this monitor.

Trained v2 model: controlled injected-corruption benchmark passed (balanced accuracy 98.63%); natural NASA author-exclusion benchmark failed (44.16%). Real inputs expose actual experimental scores for manual review only. Local tools include separate NASA-source/labelled corrupted demos, segment marks and a repeat queue. Unvalidated source-specific beat/pulse engineering estimates stay in their analysis panel, never personal vital tiles. No clinical HRV, SpO2 inference, sleep staging or astronaut validation.

Software self-check: {self.software_checks['status']}. Python {platform.python_version()} / Tk {tk.TkVersion}.''')
        window=self.root.winfo_children()[-1]
        if isinstance(window,tk.Toplevel): ttk.Button(window,text='Source details / attribution',command=self.source_details).pack(pady=4)

    def discover_references(self):
        self.startup_job=None
        if self.closed: return
        from .reference_library import discover_reference_library
        path=discover_reference_library()
        if path: self.start_reference_load(path)

    def load_nlsp(self):
        folder=filedialog.askdirectory(title='Select WinSCAT reference library or NLSP root',parent=self.root)
        if folder: self.start_reference_load(folder)

    def start_reference_load(self,folder):
        from .reference_library import load_reference_library
        self.start_reference_work(lambda:load_reference_library(folder),'Loading NASA reference cases…')

    def fetch_references(self):
        from .reference_library import fetch_reference_library,default_reference_cache,load_reference_library
        def fetch():
            result=fetch_reference_library(default_reference_cache()); return load_reference_library(result['root'])
        self.start_reference_work(fetch,'Fetching 26 public NASA reference files…')

    def start_reference_work(self,action,status):
        self.nlsp_button.configure(state='disabled'); self.fetch_button.configure(state='disabled'); self.reference_status.configure(text=status)
        result_queue=self.load_queue
        def work():
            # The worker owns plain data only. Retaining self here can move the
            # last reference to the destroyed Tk interpreter onto this thread.
            try: result_queue.put(('okay',action()))
            except Exception as exc: result_queue.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def poll_load(self):
        if self.closed: return
        try: state,result=self.load_queue.get_nowait()
        except queue.Empty: pass
        else:
            self.nlsp_button.configure(state='normal'); self.fetch_button.configure(state='normal')
            if state=='okay':
                self.reference_result=result; self.reference_status.configure(text='Separate NASA cases · units unverified · not patient matches'); self.draw_references()
            else:
                self.reference_status.configure(text='Reference library unavailable. Load or retry fetch.'); self.status.set('NASA reference load failed: '+result)
        # Reuse the bounded local poll to expire an idle intake; this changes
        # presentation only and never records evidence or sends a packet.
        if time.monotonic()-getattr(self,'cues_refreshed_at',0)>5:
            self.refresh_cues()
        self.poll_job=self.root.after(200,self.poll_load)

    def draw_references(self):
        c=self.reference_canvas; c.delete('all'); w,h=max(c.winfo_width(),280),max(c.winfo_height(),100); cards=(self.reference_result or {}).get('cards',[])[:3]
        if not cards:
            c.create_rectangle(4,8,w-4,h-8,fill=HUD_SURFACE,outline=HUD_BORDER,width=1)
            c.create_text(14,26,text='No NASA reference library loaded',anchor='w',fill=MUTED,font=('Segoe UI',10,'bold'))
            c.create_text(14,48,text='Load local WinSCAT or fetch 26 NASA files.',anchor='w',fill=MUTED,font=('Segoe UI',8))
            return
        query=(self.reference_result or {}).get('query',{})
        context=query.get('context',{})
        self.reference_status.configure(text=f'Query {query.get("identity","unavailable")} / {context.get("method","?")} / {context.get("phase","?")} · visit-order plots',wraplength=350)
        row_h=max(32,h/len(cards))
        for i,card in enumerate(cards):
            y0=i*row_h+2; y1=(i+1)*row_h-3
            c.create_rectangle(2,y0,w-2,y1,fill=HUD_SURFACE,outline=HUD_BORDER,width=1)
            c.create_text(8,y0+9,text=card['identity'],anchor='w',fill=INK,font=('Segoe UI',8,'bold'))
            c.create_text(w-8,y0+9,text=f'TOP-{i+1}',anchor='e',fill=ACCENT if i==0 else TRACE,font=('Segoe UI',7,'bold'))
            values=[p['value'] for p in card['points'] if p.get('value') is not None]
            context=card.get('context',{})
            c.create_text(8,y0+22,text=f'{card["metric"]} · n={card["count"]} · {str(card["date"])[:10]} · {context.get("method","?")}/{context.get("phase","?")}',anchor='w',fill=MUTED,font=('Segoe UI',7))
            if len(values)>1:
                low,high=min(values),max(values); left,right=w*.70,w-10; coords=[]
                span_y=max(y1-(y0+16)-4,12)
                for j,v in enumerate(values): coords += [left+j/(len(values)-1)*(right-left),y1-4-(v-low)/max(high-low,1)*span_y]
                c.create_line(*coords,fill=ACCENT if i==0 else TRACE,width=1.6)
    def call(self, action, success):
        try:
            action()
            self.status.set(success)
            self.refresh()
        except (ValueError, OSError, TypeError, OverflowError) as exc:
            self.status.set('Action not applied: ' + str(exc))
            messagebox.showerror('Action not applied', str(exc), parent=self.root)


    def pause(self):
        was_playing = self.playing
        self.playing = False
        self.consult_clock.pause()
        if was_playing and self.controller.current.mode == 'guided':
            self.controller.seek_seconds(self.consult_clock.position)
        if self.play_job is not None:
            self.root.after_cancel(self.play_job)
            self.play_job = None
        if hasattr(self, 'play_button'):
            self.play_button.configure(text='Play demo')
        if hasattr(self, 'quick_play_button') and self.quick_play_button is not None:
            self.quick_play_button.configure(text='▶ Play')


    def consultation_seconds(self):
        return (datetime.fromisoformat(self.controller.current.at)-datetime.fromisoformat('2026-11-14T09:00:00+00:00')).total_seconds()


    def change_consult_speed(self, _=None):
        self.consult_clock.set_speed(float(self.consult_speed.get().rstrip('x')))


    def toggle_play(self):
        if self.playing:
            self.pause()
            self.refresh()
            return
        if self.controller.current.mode != 'guided':
            return
        if self.consultation_seconds()>=GUIDED_END_SECONDS:
            self.status.set('Guided demo complete at 09:40. Choose 09:00 to replay; later history uses Next extended event.')
            return
        self.consult_clock.seek(self.consultation_seconds())
        self.consult_clock.play()
        self.playing = True
        self.play_button.configure(text='Pause demo')
        if hasattr(self, 'quick_play_button') and self.quick_play_button is not None:
            self.quick_play_button.configure(text='❚❚ Pause')
        self.play_tick()


    def play_tick(self):
        self.play_job = None
        if not self.playing or self.closed:
            return
        position = self.consult_clock.sample()
        self.controller.seek_seconds(position)
        second = int(position)
        if self.last_consult_second != second:
            self.last_consult_second = second
            self.refresh()
        else:
            utc_text = clock(self.controller.current.at)
            self.clock_label.configure(text=utc_text)
            if hasattr(self, 'nav_clock') and self.nav_clock is not None:
                self.nav_clock.configure(text=utc_text[:5] + ' UTC')
            self.draw_communication()
            self.setting_scale = True
            self.consult_position.set(position)
            self.setting_scale = False
        if not self.consult_clock.running:
            self.pause()
            self.refresh()
        else:
            self.play_job = self.root.after(33, self.play_tick)


    def scrub_consult(self, value):
        if self.setting_scale or self.controller.current.mode != 'guided':
            return
        self.pause()
        self.controller.seek_seconds(float(value))
        self.refresh()


    def step(self):
        self.pause()
        self.controller.step()
        self.refresh()


    def show_text(self, title, value):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.configure(bg=BG)
        window.geometry('850x620')
        box = scrolledtext.ScrolledText(window, wrap='word', bg=PLOT, fg=INK, font=('Consolas', 10), padx=14, pady=14)
        box.pack(fill='both', expand=True)
        text_content(box, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        ttk.Button(window, text='Close', command=window.destroy).pack(pady=8)


    def show_role(self, role):
        """Human-readable evidence first; technical data remains an explicit disclosure."""
        if role not in ('crew','doctor'): raise ValueError('Unknown evidence role')
        window=tk.Toplevel(self.root); window.title('Why this result? — '+('Crew onboard' if role=='crew' else 'Earth received evidence'))
        window.geometry('800x650'); window.minsize(560,480); window.configure(bg=BG)
        self.local_windows.append(window)
        tabs=ttk.Notebook(window); tabs.pack(fill='both',expand=True,padx=16,pady=16)
        summary=ttk.Frame(tabs); technical=ttk.Frame(tabs)
        tabs.add(summary,text='Evidence summary'); tabs.add(technical,text='Technical JSON / hash')
        for frame,text,font in ((summary,evidence_summary(self.model[role],self.projection[role]),('Segoe UI',12)),(technical,json.dumps({'role_evidence':self.model[role],'role_projection':self.projection[role]},ensure_ascii=False,indent=2),('Consolas',10))):
            box=scrolledtext.ScrolledText(frame,wrap='word',bg=PLOT,fg=INK,font=font,padx=18,pady=14)
            box.pack(fill='both',expand=True); text_content(box,text)
        ttk.Button(window,text='Close',command=window.destroy).pack(pady=(0,12))
        window.bind('<Escape>',lambda _:(window.destroy(),'break')[1])
        return window

    def author_dialog(self):
        draft_crew = self.controller.selected
        draft_session = self.controller.current
        draft_snapshot = self.model['doctor']['current_snapshot']['digest']
        received_rows=self.model['doctor']['current_snapshot']['payload']['observations']
        available_metrics=sorted({o['metric'] for o in received_rows if o['metric'] not in ('signal_review','assistant_review')})
        if not available_metrics:
            self.show_text('No delivered scalar measurements',
                'No scalar measurement is available for a conditional checklist. Assistant intake and signal-review records are review context, not numeric physiological conditions. Use Onboard assistant > Earth received handoff to read delivered intake.')
            return
        window = tk.Toplevel(self.root)
        window.configure(bg=BG)
        window.title('Flight Surgeon — author a measurement checklist')
        window.geometry('720x650')
        window.minsize(560,480)
        window.transient(self.root)
        window.bind('<Escape>',lambda _:window.destroy())
        shell=ttk.Frame(window,padding=18); shell.pack(fill='both',expand=True)
        form=ScrollableForm(shell)
        buttons=ttk.Frame(shell); buttons.pack(side='bottom',fill='x',pady=(12,0))
        form.pack(fill='both',expand=True)
        area=form.body
        ttk.Label(area, text='Reviewer-authored measurement workflow', font=('Segoe UI', 14, 'bold')).pack(anchor='w')
        description=ttk.Label(area, text='Uses only Earth’s delivered evidence and exact selected metric/task context. Enter acquisition/repeat steps; this prototype does not generate treatment advice.', wraplength=660)
        description.pack(fill='x', pady=9)
        area.bind('<Configure>',lambda e:description.configure(wraplength=max(300,e.width-12)),add='+')
        rule = ttk.Frame(area)
        rule.pack(fill='x', pady=8)
        metric_choice=ttk.Combobox(rule,state='readonly',values=available_metrics,width=16)
        metric_choice.set('reaction_time' if 'reaction_time' in available_metrics else available_metrics[0] if available_metrics else '')
        metric_choice.pack(side='left')
        unit_label=ttk.Label(rule,text=''); unit_label.pack(side='left',padx=3)
        def metric_unit(_=None):
            rows=[o for o in received_rows if o['metric']==metric_choice.get()]
            unit_label.configure(text=rows[-1]['unit'] if rows else 'Unavailable')
        metric_choice.bind('<<ComboboxSelected>>',metric_unit); metric_unit()
        operator = ttk.Combobox(rule, state='readonly', values=('le', 'lt', 'gt', 'ge'), width=5)
        operator.set('le')
        operator.pack(side='left', padx=8)
        threshold = ttk.Entry(rule, width=10)
        threshold.insert(0, '450')
        threshold.pack(side='left')
        operators_help=ttk.Label(area, text='le ≤  ·  lt <  ·  gt >  ·  ge ≥. Branch B uses the complementary comparison.',wraplength=660)
        operators_help.pack(fill='x')
        area.bind('<Configure>',lambda e:operators_help.configure(wraplength=max(300,e.width-12)),add='+')
        fields = []
        for label, default, height in [
            ('Original reviewer reply', 'Confirm acquisition conditions and record a repeat measurement with context for review.', 3),
            ('Branch A — one measurement/checklist step per line', 'Confirm the same task, device and acquisition conditions.\nRecord a repeat measurement and its context.', 4),
            ('Branch B — one measurement/checklist step per line', 'Confirm task and acquisition conditions before repeating.\nRecord distractions and a supported repeat measurement.\nPrepare a follow-up packet for the reviewer.', 4)]:
            ttk.Label(area, text=label).pack(anchor='w', pady=(12, 4))
            widget = scrolledtext.ScrolledText(area, height=height, width=40, wrap='word', font=('Segoe UI', 10), bg=RAISED, fg=INK, insertbackground=INK, relief='flat', padx=8, pady=6, undo=True, highlightthickness=1, highlightbackground=GRID, highlightcolor=ACCENT)
            widget.insert('1.0', default)
            widget.pack(fill='x')
            form_text_navigation(widget)
            fields.append(widget)
        def send():
            try:
                if self.controller.selected != draft_crew or self.controller.current is not draft_session:
                    raise ValueError('This draft belongs to '+draft_crew+'. Return to that unchanged session or close this draft.')
                received = self.controller.model()['doctor']['current_snapshot']
                if not received or received['digest'] != draft_snapshot:
                    raise ValueError('Received evidence changed. Close this draft and review the new snapshot.')
                self.controller.author_reply(operator.get(), threshold.get(), *[w.get('1.0', 'end-1c') for w in fields],metric=metric_choice.get())
            except (ValueError, OSError) as exc:
                messagebox.showerror('Reply not sent', str(exc), parent=window)
                return
            window.destroy()
            self.status.set('Reviewer-authored checklist in transit; crew receives it after 20 synthetic minutes.')
            self.refresh()
        ttk.Button(buttons, text='Send authored checklist', style='Accent.TButton', command=send).pack(side='left')
        ttk.Button(buttons, text='Cancel', command=window.destroy).pack(side='right')
        metric_choice.focus_set()
        return window


    def save_to(self, directory):
        try:
            path = self.controller.save(directory)
        except (OSError, ValueError) as exc:
            self.status.set('Save not applied: ' + str(exc))
            messagebox.showwarning('Choose another save folder', f'{exc}\n\nSelect another folder to save the validated session.', parent=self.root)
            alternate = filedialog.askdirectory(title='Alternate session folder', parent=self.root)
            if alternate:
                self.call(lambda: self.controller.save(alternate), 'Session saved in the selected alternate folder.')
            return
        self.status.set('Session saved: ' + path)


    def save(self):
        self.save_to(default_sessions_directory())


    def export(self):
        directory = filedialog.askdirectory(title='Export validated session into folder', parent=self.root)
        if directory:
            self.save_to(directory)


    def open_session(self):
        self.stop_signal()
        self.pause()
        path = filedialog.askopenfilename(title=f'Open {self.controller.selected} demo or recorded local session',
            filetypes=[('BioTwin session JSON', '*.json')], parent=self.root)
        if path:
            def open_validated():
                self.controller.open(path)
                if self.controller.current.clock_basis=='inferred':
                    messagebox.showinfo('Older session clock inferred',
                        'This older file did not save its replay clock. Time was inferred from actual event times; scheduled packet arrivals were not used.',parent=self.root)
            self.call(open_validated, 'Validated session opened for the selected crew. Other crew preserved.')


    def change_source(self, _=None):
        self.stop_signal()
        for channel, panel in self.signal_panels.items():
            example = next((e for e in self.examples if e['crew'] == self.controller.selected and e['channel'].upper() == channel), None)
            record = self.record_cache[example['file']] if example else None
            panel['record'] = record
            panel['window'] = None
            if record:
                record.playback.pause()
                record.playback.seek(0)
                record.playback.set_speed(float(panel['speed'].get().rstrip('x')))
                panel['scale'].configure(to=record.playback.end, state='normal')
                panel['button'].configure(state='normal')
                panel['speed'].configure(state='readonly')
            else:
                panel['scale'].configure(state='disabled')
                panel['button'].configure(state='disabled')
                panel['speed'].configure(state='disabled')
        self.draw_signal()


    def draw_signal(self, channel=None):
        if not hasattr(self, 'signal_panels'):
            return
        channels = (channel,) if channel else tuple(self.signal_panels)
        for name in channels:
            panel = self.signal_panels[name]
            record, canvas = panel['record'], panel['chart']
            canvas.delete('all')
            w, h = max(250, canvas.winfo_width()), max(75, canvas.winfo_height())
            if record is None:
                canvas.create_text(w/2, h/2-10, text=name+' UNAVAILABLE', fill=MUTED, font=('Segoe UI', 11, 'bold'))
                canvas.create_text(w/2, h/2+17, text='No bundled source channel', fill=MUTED, font=('Segoe UI', 10))
                panel['stamp'].configure(text='-- source seconds')
                panel['caption'].configure(text='Unavailable')
                continue
            result = record.window()
            panel['window'] = result
            left, right, top, bottom = 46, w-10, 19, h-22
            values = [p['value'] for p in result['points'] if p['value'] is not None]
            low, high = (min(values), max(values)) if values else (0, 1)
            pad = max((high-low)*.08, .001)
            low, high = low-pad, high+pad
            start, end = result['source_start_seconds'], result['source_end_seconds']
            x = lambda t: left+(t-start)/max(end-start, .001)*(right-left)
            y = lambda v: bottom-(v-low)/(high-low)*(bottom-top)
            for overlay in result['author_exclusions']:
                lo, hi = max(start, overlay['start']), min(end, overlay['end'])
                if lo <= hi:
                    canvas.create_rectangle(x(lo), top, x(hi), bottom, fill=EXCLUDED, outline='')
            for fraction in (0, .25, .5, .75, 1):
                v = low+(high-low)*fraction
                canvas.create_line(left, y(v), right, y(v), fill=GRID)
                if fraction in (0, .5, 1):
                    canvas.create_text(left-7, y(v), text=f'{v:.2f}', anchor='e', fill=MUTED, font=('Consolas', 8))
                t = start+(end-start)*fraction
                canvas.create_line(x(t), top, x(t), bottom, fill=GRID)
                canvas.create_text(x(t), bottom+12, text=f'{t:.1f}s', fill=MUTED, font=('Consolas', 8))
            segment = []
            for point in result['points']:
                if point['value'] is None:
                    if len(segment) >= 4:
                        canvas.create_line(*segment, fill=TRACE, width=1.4)
                    segment = []
                else:
                    segment += [x(point['source_seconds']), y(point['value'])]
            if len(segment) >= 4:
                canvas.create_line(*segment, fill=TRACE, width=1.4)
            cursor = x(result['cursor'])
            canvas.create_line(cursor, top, cursor, bottom, fill=INK, width=2)
            canvas.create_polygon(cursor-4, top-5, cursor+4, top-5, cursor, top+1, fill=INK)
            canvas.create_text(left, 12, text='Source amplitude / units unverified', anchor='w', fill=MUTED, font=('Segoe UI', 8))
            state = 'REPLAYING' if record.playback.running else ('END / REPLAY' if record.playback.position >= record.playback.end else 'PAUSED / REPLAY')
            panel['stamp'].configure(text=f'{result["cursor"]:.1f}s')
            panel['button'].configure(text='Pause' if record.playback.running else 'Replay' if record.playback.position >= record.playback.end else 'Play')
            panel['caption'].configure(text=f'{record.example["source_key"].split("/")[0]} / {record.example["nominal_rate_hz"]:g} Hz')
            self.setting_scale = True
            panel['position'].set(record.playback.position)
            self.setting_scale = False
        flags = sum(bool(p['window'] and p['window']['quality_reasons']) for p in self.signal_panels.values())
        self.source_status.configure(text=('NASA SOTERIA recording / independent source clocks / separate participants' if self.examples else 'SOURCE UNAVAILABLE: '+str(self.assets_error)))
        if self.selected_region=='heart': self.draw_cardiac_focus()


    def toggle_signal(self, channel='ECG'):
        panel = self.signal_panels[channel]
        if panel['record'] is None:
            return
        playback = panel['record'].playback
        if playback.running:
            playback.pause()
        else:
            playback.play()
        self.draw_signal(channel)
        self.schedule_signal()


    def change_signal_speed(self, channel):
        panel = self.signal_panels[channel]
        if panel['record']:
            panel['record'].playback.set_speed(float(panel['speed'].get().rstrip('x')))


    def reset_signal(self, channel):
        panel = self.signal_panels[channel]
        if panel['record']:
            panel['record'].playback.pause()
            panel['record'].playback.seek(0)
            self.draw_signal(channel)
            self.schedule_signal()


    def scrub_signal(self, channel, value):
        if self.setting_scale:
            return
        panel = self.signal_panels[channel]
        if panel['record']:
            panel['record'].playback.pause()
            panel['record'].playback.seek(float(value))
            self.draw_signal(channel)
            self.schedule_signal()


    def schedule_signal(self):
        if self.signal_job is not None:
            self.root.after_cancel(self.signal_job)
            self.signal_job = None
        self.signal_playing = any(p['record'] and p['record'].playback.running for p in self.signal_panels.values())
        if self.signal_playing and not self.closed:
            self.signal_job = self.root.after(33, self.signal_tick)


    def signal_tick(self):
        self.signal_job = None
        if self.closed:
            return
        now = time.monotonic()
        for panel in self.signal_panels.values():
            if panel['record']:
                panel['record'].playback.sample(now)
        self.draw_signal()
        self.schedule_signal()


    def stop_signal(self):
        self.signal_playing = False
        if self.signal_job is not None:
            self.root.after_cancel(self.signal_job)
            self.signal_job = None
        for panel in getattr(self, 'signal_panels', {}).values():
            if panel['record']:
                panel['record'].playback.pause()
                panel['button'].configure(text='Play')


    def source_details(self):
        details = {'recording': 'NASA SOTERIA / preserved offline source. Synthetic consultation identities are separate.',
            'timing': 'Each channel has its own source clock. Synchronization and amplitude units unverified.',
            'display': '10-second min/max windows, at most 900 display points per channel. Cursor uses elapsed monotonic time.',
            'marks': 'Shading is strict author-exclusion interior mapped from original sample positions. Endpoints excluded. Outside marks does not mean clean. ECG author heartbeat-detector exclusions are not disease labels; BVP has no supervised annotations.',
            'capabilities': 'NASA source replay remains separate. Laptop tools support explicit manual vitals/sleep diary, uncalibrated keyboard task, local imports and experimental technical review; engineering beat/pulse estimates remain source-specific. No clinical HRV, inferred SpO2, sleep staging, live hardware or clinical verdict.',
            'channels': []}
        for name, panel in self.signal_panels.items():
            record = panel['record']
            if record:
                details['channels'].append({'channel': name, 'engineering_flags': panel['window']['quality_reasons'] if panel['window'] else [],
                    'provenance': {k: v for k, v in record.example.items() if k not in ('times', 'values')}})
            else:
                details['channels'].append({'channel': name, 'status': 'unavailable: no bundled record for selected crew'})
        self.show_text('NASA recording / source details', details)


    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.presentation_host is not None:
            self.presentation_host.close()
            self.presentation_host=None
        self.feedback.close()
        if self.introduction: self.introduction.close()
        if self.intro_job is not None:
            self.root.after_cancel(self.intro_job); self.intro_job=None
        for dialog in list(self.assistant_dialogs): dialog.close()
        for cancel in list(self.local_cancels):
            cancel()
        if self.startup_job is not None:
            self.root.after_cancel(self.startup_job)
            self.startup_job = None
        self.pause()
        self.stop_signal()
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        self.crew_images.close()
        self.root.destroy()
        self.local_cancels.clear()
        self.local_windows.clear()
        self.assistant_dialogs.clear()
        for name in ('stage_buttons','quick_stage_buttons','crew_cards','sensor_tiles','step_widgets','signal_panels','region_buttons'):
            getattr(self,name).clear()
        # Closed apps can remain referenced by callers. Release all direct Tk
        # owners now on the creator thread, rather than in a later worker GC.
        for name,value in tuple(vars(self).items()):
            if isinstance(value,(tk.Misc,tk.Variable)):
                setattr(self,name,None)
def smoke_test():
    report = {'format': 'biotwin-desktop-smoke', 'version': __version__, 'python': platform.python_version(),
              'platform': platform.platform(), 'frozen': bool(getattr(sys, 'frozen', False)), 'checks': {}}
    root = None
    try:
        report['backend'] = self_check()
        report['checks']['backend'] = report['backend']['status'] == 'passed'
        examples = load_examples()
        report['checks']['assets'] = len(examples) == 3 and all(preview(e, e['times'][0])['sample_count'] > 0 for e in examples)
        from .presentation import smoke_check as presentation_smoke
        report['presentation']=presentation_smoke()
        report['checks']['presentation_http']=all(report['presentation'].values())
        report['assets'] = {'verified_count': len(examples), 'examples': [
            {'file': e['file'], 'samples': len(e['times']), 'original_sample_start': e['sample_start'],
             'channel': e['channel'], 'source_sha256': e['source_sha256']} for e in examples]}
        controller = DesktopController()
        controller.seek(len(STOPS)-1)
        expected = controller.document()
        with tempfile.TemporaryDirectory(prefix='biotwin-smoke-') as directory:
            path = controller.save(directory)
            controller.open(path)
            report['checks']['session_roundtrip'] = expected == controller.document()
        report['checks']['role_isolation'] = len(controller.model()['doctor']['followups']) == 1 and not controller.sessions[CREWS[1]].workflow.state(controller.sessions[CREWS[1]].at)['doctor']['followups']
        # Packaged functional checks use temporary deterministic QA evidence;
        # no external sensors, network services or visible acquisition dialogs.
        from .local_monitoring import export_import_examples
        local_controller=DesktopController(); local_controller.start_local()
        at=datetime.now(timezone.utc).isoformat()
        local_controller.capture_manual({'subject_id':local_controller.subject,'source_id':'smoke-test-only',
            'metric':'heart_rate','value':72,'unit':'bpm','method':'deterministic QA fixture, not a person',
            'observed_at':at,'quality':'usable'})
        before_delivery=mission_projection(local_controller.model())
        local_controller.send_consultation(); local_controller.advance()
        after_delivery=mission_projection(local_controller.model())
        report['checks']['local_received_only']=(before_delivery['crew']['sensors']['HR']['value']==72
            and before_delivery['doctor']['sensors']['HR']['value'] is None
            and after_delivery['doctor']['sensors']['HR']['value']==72)
        local_controller.author_reply('le',80,'QA acquisition check','Record repeat','Inspect method',metric='heart_rate')
        local_controller.advance(); local_controller.complete(0,'Explicit temporary QA completion.')
        local_controller.followup(); local_controller.advance()
        report['checks']['local_checklist_followup']=len(local_controller.model()['doctor']['followups'])==1
        with tempfile.TemporaryDirectory(prefix='biotwin-local-smoke-') as directory:
            paths=export_import_examples(directory,subject_id=local_controller.subject)
            local_controller.import_observations(next(p for p in paths if 'observation-template' in p))
            signal=local_controller.add_signal(next(p for p in paths if 'synthetic-signal-example' in p))
            local_controller.mark_segment(signal['id'],0,256,'Explicit synthetic example inspection.')
            request=local_controller.request_repeat(signal['id'],'Request supported test repeat.')
            local_controller.resolve_repeat(request['id'],'QA queue resolution.')
            expected=local_controller.document(); path=local_controller.save(directory); local_controller.open(path)
            report['checks']['local_import_save_roundtrip']=local_controller.document()==expected
            local_controller.select(CREWS[1]); other=local_controller.document()
            try: local_controller.open(path)
            except ValueError: report['checks']['local_crew_isolation']=local_controller.document()==other
            else: report['checks']['local_crew_isolation']=False
        from .reaction_check import ReactionCheck
        class SmokeClock:
            value=0.
            def __call__(self): return self.value
        class SmokeRNG:
            def uniform(self,low,high): return low
        measured_clock=SmokeClock(); reaction=ReactionCheck(clock=measured_clock,rng=SmokeRNG()); reaction.start()
        measured_clock.value=reaction.due+.25; reaction.show_stimulus(); measured_clock.value+=.3; reaction.respond()
        first=reaction.results[0]['reaction_ms']
        measured_clock.value=reaction.due; reaction.show_stimulus(); measured_clock.value+=2.01; reaction.respond()
        report['checks']['reaction_monotonic_boundaries']=abs(first-300)<1e-8 and len(reaction.results)==2 and reaction.results[1]['outcome']=='omission'
        reaction.cancel()
        from .signal_review import analyze_local_signal,create_injected_demo,load_default_model
        trained=load_default_model(); source=model_demo_signal(next(e for e in examples if e['channel'].upper()=='ECG'))
        original_analysis=analyze_local_signal(source); injected=create_injected_demo(source,family='hold')
        injected_analysis=analyze_local_signal(injected)
        report['checks']['trained_model_resource_inference']=(bool(trained['id'])
            and any(finite(w.get('technical_corruption_score')) for w in original_analysis['windows'])
            and any(finite(w.get('technical_corruption_score')) for w in injected_analysis['windows'])
            and source['source_id']!=injected['source_id'] and source['subject_id']==injected['subject_id'])
        report['model']={'id':trained['id'],'deployment':trained['deployment'],
            'natural_head_deployment':trained.get('natural_head',{}).get('deployment'),
            'scope':'Controlled injected-corruption demo; real sources require manual review. No clinical verdict.'}
        assistant_controller=DesktopController(); assistant_controller.start_local()
        catalog=assistant_controller.assistant_catalog(); protocols=catalog['protocols']
        unknown=[assistant_controller.preview_assistant(p['id'],{}) for p in protocols]
        protocol=protocols[0]; answers={q['id']:'no' for q in protocol['questions']}
        urgent_answers=dict(answers)
        urgent_answers[next(q['id'] for q in protocol['questions'] if q['kind']=='urgent')]='yes'
        report['checks']['assistant_catalog_urgent_unknown']=(len(protocols)==3
            and all(p['status']=='clarify' for p in unknown)
            and assistant_controller.preview_assistant(protocol['id'],urgent_answers)['status']=='urgent'
            and not assistant_controller.assistant_records('crew'))
        assessment=assistant_controller.assess_assistant(protocol['id'],answers,'Temporary packaged QA intake.',
            expected_context=assistant_controller.assistant_context())
        assistant_controller.complete_assistant(assessment['id'],assessment['steps'][0]['id'],
            'Explicit QA completion; no symptom-resolution claim.',expected_context=assistant_controller.assistant_context())
        assistant_controller.send_consultation()
        before=assistant_controller.assistant_records('doctor'); assistant_controller.advance()
        delivered=assistant_controller.assistant_records('doctor')
        assistant_controller.assess_assistant(protocol['id'],{},'Unsent QA reassessment.',
            expected_context=assistant_controller.assistant_context())
        report['checks']['assistant_action_delayed_handoff']=(not before and len(delivered)==2
            and len(assistant_controller.assistant_records('crew'))==3
            and len(assistant_controller.assistant_records('doctor'))==2
            and assistant_controller.assistant_handoff('doctor')['received_view_only'])
        with tempfile.TemporaryDirectory(prefix='biotwin-assistant-smoke-') as directory:
            expected=assistant_controller.document(); path=assistant_controller.save(directory)
            assistant_controller.open(path)
            report['checks']['assistant_save_open']=expected==assistant_controller.document()
        root = tk.Tk()
        root.withdraw()
        app = DesktopApp(root, controller=controller, startup=False)
        root.update_idletasks()
        root.update()
        report['checks']['tk_construct_update'] = app.screen == 'home' and len(app.crew_cards) == 2 and root.winfo_exists() == 1
        presentation=app.open_presentation(open_browser=False)
        presentation_thread=presentation.thread if presentation else None
        report['checks']['presentation_launch']=(presentation is not None and app.open_presentation(open_browser=False) is presentation)
        before_tour=controller.document()
        tour=app.show_introduction(); tour.window.withdraw(); tour.forward(); tour.backward(); tour.close()
        report['checks']['ux_tour_preserves_session']=controller.document()==before_tour and app.introduction is None
        app.toggle_motion(); app.open_monitor(CREWS[0])
        report['checks']['ux_reduced_motion']=not app.signal_playing and app.signal_job is None
        report['checks']['ux_three_panels']=all(w.winfo_manager() for w in (app.condition_panel,app.action_panel,app.earth_panel)) and not app.timeline_area.winfo_manager()
        controller.reset()
        app.open_monitor(CREWS[0])
        app.jump(3)
        root.update_idletasks()
        report['checks']['monitor_plan_b'] = app.projection['crew']['checklist']['branch'] == 'B' and len(app.step_widgets) == 3
        report['checks']['paired_role_isolation'] = app.projection['crew']['metric']['value'] == 585 and app.projection['doctor']['metric']['value'] == 420
        controller.start_local(); app.refresh()
        assistant=app.onboard_assistant(); assistant.window.withdraw()
        urgent_index=next(i for i,q in enumerate(assistant.protocol['questions']) if q['kind']=='urgent')
        assistant.index=urgent_index; assistant.update_question(); assistant.answer_buttons[0].invoke()
        root.update_idletasks(); root.update()
        report['checks']['assistant_tk_live_urgent']=(assistant.preview['status']=='urgent'
            and 'URGENT' in assistant.immediate.cget('text') and not controller.assistant_records('crew'))
        assistant.close()
        report['tk'] = {'tcl_patchlevel': root.tk.call('info', 'patchlevel'),
                        'tk_patchlevel': root.tk.call('package', 'present', 'Tk'), 'home': 'crew overview', 'monitor': 'paired crew / Flight Surgeon'}
        app.close()
        report['checks']['presentation_shutdown']=presentation_thread is not None and not presentation_thread.is_alive()
        root = None
        report['checks']['tk_destroy'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if root is not None:
            root.destroy()
    report['status'] = 'passed' if report['checks'] and all(report['checks'].values()) and 'error' not in report else 'failed'
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='BioTwin Sync astronaut health monitoring system')
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--presentation', action='store_true', help='Open the bundled crew / Flight surgeon browser presentation')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    if args.report and (args.report.name.casefold() == '.env' or args.report.name.casefold().startswith('.env.') or args.report.suffix.lower() != '.json'):
        parser.error('Report must be a non-credential .json path.')
    if args.smoke_test:
        report = smoke_test()
        raw = json.dumps(report, ensure_ascii=False, indent=2)+'\n'
        if args.report:
            # Smoke reports never replace existing user files.
            with args.report.open('x', encoding='utf-8') as handle:
                handle.write(raw)
        if sys.stdout is not None:
            print(raw)
        return 0 if report['status'] == 'passed' else 1
    root = tk.Tk()
    app=DesktopApp(root,startup=not args.presentation)
    if args.presentation: root.after_idle(app.open_presentation)
    root.mainloop()
    return 0
