"""Compact, context-bound offline assistant. No network or clinical model calls."""
from __future__ import annotations

import tkinter as tk
import time
from tkinter import ttk, scrolledtext

from .desktop_theme import AMBER, BG, INK, MUTED, PLOT, RED, apply_theme, primary_button
from .desktop_ux import GradientText, form_text_navigation


def _text(widget, value):
    widget.configure(state='normal')
    widget.delete('1.0', 'end')
    widget.insert('1.0', value)
    widget.configure(state='disabled')


class AssistantDialog:
    """A draft belongs to one controller incarnation and exact evidence context.

    The host refreshes ``check_context`` after mutations. A small local timer
    catches changes made by other nonmodal tools without transferring answers.
    Preview is pure, so an urgent answer is visible before a record is saved.
    """

    def __init__(self, root, controller, *, refresh=lambda: None, tools=None,
                 start_local=None, on_close=None):
        self.controller = controller
        self.refresh_host = refresh
        self.tools = tools or {}
        self.start_local = start_local
        self.on_close = on_close
        self.catalog = controller.assistant_catalog()
        self.bound_crew = controller.selected
        self.bound_session = controller.current
        self.context = controller.assistant_context()
        self.stale = False
        self.saved = None
        self.closed = False
        self.job = None
        self.index = 0
        self.history_refresh_at = 0.0
        self.history_records = []
        self.answers = {}
        self.action_widgets = []
        self.protocols = self.catalog['protocols']
        self.protocol = self.protocols[0]
        self.window = tk.Toplevel(root)
        self.window.title('Onboard assistant / ' + self.bound_crew)
        self.window.geometry('760x720')
        self.window.minsize(570, 520)
        self.window.configure(bg=BG)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.window.bind('<Destroy>', self._window_destroyed, add='+')
        self.window.bind('<Escape>', lambda _: self.close())
        self.stage = 'questions'
        self.notes_open = False
        self.action_notes_open = False
        self.link_open = False
        style = apply_theme(self.window)
        style.configure('AssistantPrimary.TButton', font=('Segoe UI', 10, 'bold'), padding=(12, 7))
        primary_button(style, 'AssistantPrimary.TButton')
        style.configure('AssistantTitle.TLabel', font=('Segoe UI', 15, 'bold'), foreground=INK)
        # Three regions: fixed safety, one scrollable stage, fixed navigation.
        self.header = ttk.Frame(self.window, padding=(14, 10))
        self.header.pack(fill='x')
        title = ttk.Frame(self.header)
        title.pack(fill='x')
        ttk.Label(title, text='Onboard assistant', style='AssistantTitle.TLabel').pack(side='left')
        self.urgent_button = ttk.Button(title, text='Get urgent help', command=self.urgent_help)
        self.urgent_button.pack(side='right')
        self.safety = ttk.Label(self.header, wraplength=700, foreground=AMBER,
            text='Source-linked prototype · not clinician reviewed')
        self.safety.pack(fill='x', pady=(3, 0))
        self.immediate = ttk.Label(self.header, wraplength=700, foreground=AMBER)
        self.immediate.pack(fill='x', pady=(6, 0))
        self.urgent_history = ttk.Label(self.header, wraplength=700, foreground=RED)
        self.context_label = ttk.Label(self.header, wraplength=700, foreground=MUTED)
        self.context_label.pack(fill='x', pady=(5, 0))
        self.begin_button = ttk.Button(self.header, text='Start local session (replaces workspace)', command=self.begin_local)
        if self.bound_session.mode != 'local':
            self.begin_button.pack(anchor='w', pady=(6, 0))
        # Create the content before the footer for natural Tab traversal, but
        # pack the footer first to reserve its visible area at small sizes.
        shell = ttk.Frame(self.window)
        # Reserve the footer before the expanding canvas, including at 570x520.
        self.footer = ttk.Frame(self.window, padding=(14, 8))
        self.footer.pack(side='bottom', fill='x')
        nav = ttk.Frame(self.footer)
        nav.pack(fill='x')
        self.back = ttk.Button(nav, text='Back', command=self.go_back)
        self.back.pack(side='left')
        self.next = ttk.Button(nav, text='Next question', style='AssistantPrimary.TButton', command=self.go_next)
        self.record_button = ttk.Button(nav, text='Record assessment', style='AssistantPrimary.TButton', command=self.record)
        self.send_button = ttk.Button(nav, text='Send consultation', style='AssistantPrimary.TButton', command=self.send)
        ttk.Button(nav, text='Close', command=self.close).pack(side='right')
        self.more_button = ttk.Menubutton(nav, text='More')
        self.more_button.pack(side='right', padx=6)
        self.more_menu = tk.Menu(self.more_button, tearoff=False)
        self.more_button.configure(menu=self.more_menu)
        self.more_menu.add_command(label='Read sources & limitations', command=self.show_details)
        self.more_menu.add_command(label='Review answers & reasons', command=self.show_answers)
        self.more_menu.add_command(label='Read crew history', command=lambda: self.handoff('crew'))
        self.more_menu.add_command(label='Read Earth received handoff', command=lambda: self.handoff('doctor'))
        self.more_menu.add_separator()
        self.more_menu.add_command(label='Show link simulation', command=self.toggle_link)
        self.status = ttk.Label(self.footer, wraplength=710, foreground=MUTED)
        self.status.pack(fill='x', pady=(5, 0))
        self.help_label = ttk.Label(self.footer, wraplength=710, foreground=MUTED,
                                   text='Unsure answers stay unknown. Nothing is sent automatically.')
        self.help_label.pack(fill='x', pady=(3, 0))
        shell.pack(fill='both', expand=True, padx=14)
        self.canvas = tk.Canvas(shell, bg=BG, highlightthickness=0, takefocus=True)
        scrollbar = ttk.Scrollbar(shell, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.body = ttk.Frame(self.canvas)
        self.body_id = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize)
        self.window.bind('<MouseWheel>', self.scroll)
        self.canvas.bind('<Next>', lambda _: self.canvas.yview_scroll(1, 'pages'))
        self.canvas.bind('<Prior>', lambda _: self.canvas.yview_scroll(-1, 'pages'))
        self.window.bind('<FocusIn>', self.reveal_focus, add='+')
        stages = ttk.Frame(self.body)
        stages.pack(fill='x', pady=(2, 6))
        self.questions_button = ttk.Button(stages, text='1 · Questions', command=lambda: self.show_stage('questions'))
        self.questions_button.pack(side='left')
        self.guidance_button = ttk.Button(stages, text='2 · Guidance', command=lambda: self.show_stage('guidance'))
        self.guidance_button.pack(side='left', padx=6)
        self.questions_frame = ttk.Frame(self.body)
        self.choice = ttk.Combobox(self.questions_frame, state='readonly',
            values=[p['title'] for p in self.protocols])
        self.choice.current(0)
        self.choice.pack(fill='x', pady=(2, 8))
        self.choice.bind('<<ComboboxSelected>>', self.change_protocol)
        self.progress = ttk.Label(self.questions_frame, foreground=MUTED)
        self.progress.pack(anchor='w')
        self.question = GradientText(self.questions_frame, wraplength=690, font=('Segoe UI', 12))
        self.question.pack(fill='x', pady=(5, 5))
        options = ttk.Frame(self.questions_frame)
        options.pack(fill='x')
        self.answer = tk.StringVar(master=self.window, value='unsure')
        self.answer_buttons = []
        for title, value in (('Yes', 'yes'), ('No', 'no'), ('Unsure', 'unsure')):
            button = ttk.Radiobutton(options, text=title, value=value, variable=self.answer,
                                     command=self.answer_changed)
            button.pack(side='left', padx=(0, 22), pady=5)
            self.answer_buttons.append(button)
        self.note_button = ttk.Button(self.questions_frame, text='Add a note', command=self.toggle_notes)
        self.note_button.pack(anchor='w', pady=(14, 5))
        self.note_frame = ttk.Frame(self.questions_frame)
        ttk.Label(self.note_frame, text='Optional · 1,000 characters · kept verbatim, not interpreted',
                  foreground=MUTED, wraplength=490).pack(anchor='w')
        self.note = tk.Text(self.note_frame, height=3, bg=PLOT, fg=INK, insertbackground=INK,
                            wrap='word', font=('Segoe UI', 10), undo=True)
        self.note.pack(fill='x', pady=(3, 9))
        form_text_navigation(self.note)
        self.note.bind('<KeyRelease>', lambda _: self.update_preview())
        self.guidance_frame = ttk.Frame(self.body)
        ttk.Label(self.guidance_frame, text='Your next step', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.next_step = GradientText(self.guidance_frame, wraplength=690, foreground=AMBER, font=('Segoe UI', 11))
        self.next_step.pack(fill='x', pady=(8, 5))
        self.reason_button = ttk.Button(self.guidance_frame, text='Review answers & reasons', command=self.show_answers)
        self.reason_button.pack(anchor='w', pady=(0, 10))
        self.steps_frame = ttk.Frame(self.guidance_frame)
        self.steps_frame.pack(fill='x')
        self.action_note_button = ttk.Button(self.guidance_frame, text='Add completion note', command=self.toggle_action_notes)
        self.action_note_frame = ttk.Frame(self.guidance_frame)
        ttk.Label(self.action_note_frame, text='An action is not symptom resolution.', foreground=MUTED).pack(anchor='w')
        self.action_note = ttk.Entry(self.action_note_frame)
        self.action_note.pack(fill='x', pady=(3, 5))
        self.reassess_button = ttk.Button(self.guidance_frame, text='Reassess', command=self.reassess)
        self.reassess_button.pack(anchor='w', pady=(12, 8))
        self.link_frame = ttk.Frame(self.body)
        ttk.Label(self.link_frame, text='Simulated link · delivered snapshots only', foreground=MUTED).pack(anchor='w')
        self.advance_button = ttk.Button(self.link_frame, text='Advance link +20 min', command=self.advance)
        self.advance_button.pack(anchor='w', pady=(4, 8))
        for widget, help_text in (
            (self.urgent_button, 'Show emergency instructions now. No alert is transmitted.'),
            (self.begin_button, 'Replace the current workspace with an empty laptop-local session.'),
            (self.choice, 'Choose a concern. Changing it starts a fresh intake; prior records remain.'),
            (self.next, 'Move to the next question, then review guidance before recording.'),
            (self.record_button, 'Save this assessment locally, including any unknown answers. Does not send.'),
            (self.send_button, 'Queue a consultation on the simulated delayed Earth link. Not emergency help.'),
            (self.more_button, 'Open sources, answers, crew history, Earth received history and link simulation.'),
            (self.reassess_button, 'Start fresh questions against current evidence. Prior urgent reports remain.'),
            (self.advance_button, 'Advance the simulated delivery clock by 20 minutes; no live network.')):
            self.explain(widget, help_text)
        self._reset_answers()
        self.update_question()
        self.show_stage('questions')
        self.check_context()
        self.job = self.window.after(250, self.poll)

    def explain(self, widget, message):
        """The same explanation is available to pointer and keyboard users."""
        for event in ('<Enter>', '<FocusIn>'):
            widget.bind(event, lambda _, text=message: self.help_label.configure(text=text), add='+')
        for event in ('<Leave>', '<FocusOut>'):
            widget.bind(event, lambda _: self.help_label.configure(
                text='Unsure answers stay unknown. Nothing is sent automatically.'), add='+')

    def show_stage(self, stage, *, focus=False):
        self.stage = stage
        self.questions_frame.pack_forget()
        self.guidance_frame.pack_forget()
        for button in (self.next, self.record_button, self.send_button):
            button.pack_forget()
        if stage == 'questions':
            self.questions_frame.pack(fill='x', before=self.link_frame if self.link_open else None)
            self.next.pack(side='left', padx=8)
            target = self.answer_buttons[0]
        else:
            self.guidance_frame.pack(fill='x', before=self.link_frame if self.link_open else None)
            button = self.record_button if self.saved is None else self.send_button
            button.pack(side='left', padx=8)
            target = button
        self.questions_button.state(['pressed'] if stage == 'questions' else ['!pressed'])
        self.guidance_button.state(['pressed'] if stage == 'guidance' else ['!pressed'])
        self.canvas.yview_moveto(0)
        self.check_context()
        if focus:
            target.focus_set()

    def go_next(self):
        if self.index == len(self.protocol['questions']) - 1:
            self.show_stage('guidance', focus=True)
        else:
            self.navigate(1)
            self.answer_buttons[0].focus_set()

    def go_back(self):
        if self.stage == 'guidance':
            self.show_stage('questions', focus=True)
        else:
            self.navigate(-1)

    def toggle_notes(self):
        self.notes_open = not self.notes_open
        self.note_button.configure(text='Hide note' if self.notes_open else 'Add a note')
        if self.notes_open:
            self.note_frame.pack(fill='x')
            self.note.focus_set()
        else:
            self.note_frame.pack_forget()
            self.note_button.focus_set()

    def toggle_action_notes(self):
        self.action_notes_open = not self.action_notes_open
        self.action_note_button.configure(text='Hide completion note' if self.action_notes_open else 'Add completion note')
        if self.action_notes_open:
            self.action_note_frame.pack(fill='x', before=self.reassess_button)
            self.action_note.focus_set()
        else:
            self.action_note_frame.pack_forget()
            self.action_note_button.focus_set()

    def toggle_link(self):
        self.link_open = not self.link_open
        if self.link_open:
            self.link_frame.pack(fill='x', pady=(14, 0))
            self.advance_button.focus_set()
        else:
            self.link_frame.pack_forget()
        self.more_menu.entryconfigure(5, label='Hide link simulation' if self.link_open else 'Show link simulation')

    def show_answers(self):
        result = self.preview
        titles = {q['id']: q['text'] for q in self.protocol['questions']}
        reasons = [titles.get(i, i) for i in result['reasons']]
        lines = [self.protocol['title'], 'Preview · ' + result['status'].upper(),
                 'Reasons: ' + ('; '.join(reasons) if reasons else
                                'reported answers and remaining uncertainty; no all-clear.'),
                 'Unknown answers: ' + str(len(result['missing_answers'])), '']
        lines.extend(q['text'] + '\nAnswer: ' + self.answers[q['id']].capitalize()
                     for q in self.protocol['questions'])
        self.show_text('Answers & reasons', '\n\n'.join(lines))

    def resize(self, event):
        self.canvas.itemconfigure(self.body_id, width=event.width)
        width = max(260, event.width - 12)
        for widget in (self.question, self.next_step):
            widget.configure(wraplength=width)
        self.safety.configure(wraplength=width)
        self.context_label.configure(wraplength=width)
        self.immediate.configure(wraplength=width)
        self.urgent_history.configure(wraplength=width)
        self.status.configure(wraplength=width)
        self.help_label.configure(wraplength=width)
        for widget in self.steps_frame.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=width)

    def scroll(self, event):
        if isinstance(event.widget, (tk.Text, scrolledtext.ScrolledText)):
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def reveal_focus(self, event):
        widget = event.widget
        parent = widget
        while parent is not self.body and parent is not self.window:
            parent = getattr(parent, 'master', None)
            if parent is None:
                return
        if parent is self.body:
            y = widget.winfo_rooty() - self.body.winfo_rooty()
            top = self.canvas.canvasy(0)
            if y < top or y + widget.winfo_height() > top + self.canvas.winfo_height():
                self.canvas.yview_moveto(max(0, y - 12) / max(1, self.body.winfo_height()))

    def _reset_answers(self):
        self.answers = {q['id']: 'unsure' for q in self.protocol['questions']}
        self.index = 0
        self.saved = None

    def same_session(self):
        return (self.controller.selected == self.bound_crew
                and self.controller.current is self.bound_session)

    def check_context(self):
        if self.closed:
            return False
        current = self.same_session() and self.controller.assistant_context() == self.context
        if self.same_session() and time.monotonic() >= self.history_refresh_at:
            self.history_records = self.controller.assistant_records('crew')
            self.history_refresh_at = time.monotonic() + 2.0
            urgent_count = sum(r.get('kind') == 'assessment' and r.get('status') == 'urgent' for r in self.history_records)
            self.urgent_history.configure(text=(
                f'{self.bound_crew}: {urgent_count} prior urgent report(s) retained. New answers / completed actions do not resolve them; contact medical officer / clinician.'
                if urgent_count else ''))
            if urgent_count:
                self.urgent_history.pack(fill='x', before=self.context_label, pady=(4, 0))
            else:
                self.urgent_history.pack_forget()
        if current and self.saved is not None:
            plan = next((r for r in self.history_records if r.get('id') == self.saved['id']), None)
            if plan is None or plan.get('stale'):
                self.stale = True
        if not current:
            self.stale = True
        local = self.bound_session.mode == 'local'
        enabled = current and not self.stale and local
        self.record_button.configure(state='normal' if enabled and self.saved is None and len(self.note.get('1.0', 'end-1c')) <= 1000 else 'disabled')
        self.send_button.configure(state='normal' if enabled else 'disabled')
        self.advance_button.configure(state='normal' if current and not self.stale else 'disabled')
        for widget in self.action_widgets:
            widget.configure(state='normal' if enabled else 'disabled')
        for widget in self.answer_buttons:
            widget.configure(state='normal' if current and not self.stale and self.saved is None else 'disabled')
        self.choice.configure(state='readonly' if current and not self.stale else 'disabled')
        self.next.configure(state='normal' if current and not self.stale else 'disabled',
                            text='Review guidance' if self.index == len(self.protocol['questions']) - 1 else 'Next question')
        self.back.configure(state='normal' if self.stage == 'guidance' or self.index > 0 else 'disabled')
        self.reassess_button.configure(state='normal' if self.same_session() else 'disabled')
        self.begin_button.configure(state='normal' if self.same_session() else 'disabled')
        self.note.configure(state='normal' if current and not self.stale and self.saved is None else 'disabled')
        if self.stale:
            self.context_label.configure(text='Context changed or plan expired: this draft / plan is locked. Choose Guidance → Reassess; reopen for another crew/session. Expiry is an engineering limit, not a medically safe waiting time.', foreground=AMBER)
        else:
            self.context_label.configure(text=(f'{self.bound_crew} · current observations' if local else
                f'Preview for {self.bound_crew}; start a local session to record.'), foreground=MUTED)
        return enabled

    def poll(self):
        self.job = None
        if not self.closed:
            self.check_context()
            self.job = self.window.after(250, self.poll)

    def begin_local(self):
        if not self.same_session():
            self.check_context()
            return
        self.controller.start_local()
        self.bound_session = self.controller.current
        self.context = self.controller.assistant_context()
        self.begin_button.pack_forget()
        self.stale = False
        self.reassess()
        self.refresh_host()

    def change_protocol(self, _=None):
        if self.stale or not self.same_session():
            self.check_context()
            return
        self.protocol = self.protocols[self.choice.current()]
        self._reset_answers()
        self.note.configure(state='normal')
        self.note.delete('1.0', 'end')
        self.render_steps()
        self.update_question()
        self.show_stage('questions')

    def update_question(self):
        q = self.protocol['questions'][self.index]
        self.progress.configure(text=f'Question {self.index + 1} of {len(self.protocol["questions"])} · Unsure = unknown')
        self.question.configure(text=q['text'])
        self.answer.set(self.answers[q['id']])
        self.check_context()
        self.update_preview()

    def answer_changed(self):
        if self.stale or self.saved is not None or not self.same_session():
            self.check_context()
            return
        self.answers[self.protocol['questions'][self.index]['id']] = self.answer.get()
        self.update_preview()

    def navigate(self, delta):
        if self.stale or not self.same_session():
            self.check_context()
            return
        self.index = max(0, min(len(self.protocol['questions']) - 1, self.index + delta))
        self.update_question()

    def update_preview(self):
        if self.closed:
            return
        try:
            # Narrative never influences urgency, including when its length is invalid.
            self.preview = self.controller.preview_assistant(self.protocol['id'], self.answers)
            result = self.preview
            unknown = f'\nUnknown answers: {len(result["missing_answers"])}.' if result['missing_answers'] else ''
            self.immediate.configure(text=(
                'URGENT · Reported warning sign. Alert onboard medical officer / crew now and use the established emergency procedure. Do not wait for Earth. Actual desktop emergency: seek local emergency assistance.'
                if result['status'] == 'urgent' else
                {'clarify': 'CLARIFY · Unknown safety answers need clarification / contact; no all-clear.',
                 'review': 'REVIEW · Contact the onboard medical officer / clinician for reported concerns.',
                 'collect': 'COLLECT · Record evidence and reassess; this is not clinical clearance.'}.get(result['status'], result['status'])),
                foreground=RED if result['status'] == 'urgent' else AMBER)
            self.next_step.configure(text=result['next_step'] + unknown,
                                     foreground=RED if result['status'] == 'urgent' else AMBER)
            if len(self.note.get('1.0', 'end-1c')) > 1000:
                self.status.configure(text='Note exceeds 1,000 characters. Shorten it before recording. Urgent advice remains active.')
            self.check_context()
        except (ValueError, OSError, TypeError) as exc:
            self.next_step.configure(text='Preview unavailable: ' + str(exc), foreground=AMBER)
            self.immediate.configure(text='Preview unavailable · urgent help remains available independently.', foreground=AMBER)

    def urgent_help(self):
        self.show_text('Urgent help · act now; nothing transmitted', self.catalog['urgent_help'] +
                       '\n\nThis button shows instructions only. No alert is transmitted. Do not wait for saving, completing questions or the simulated Earth link.')

    def record(self):
        if not self.check_context():
            return
        try:
            self.saved = self.controller.assess_assistant(self.protocol['id'], self.answers,
                self.note.get('1.0', 'end-1c'), expected_context=self.context)
            self.context = self.controller.assistant_context()
            self.history_refresh_at = 0.0
            self.status.configure(text='Assessment recorded locally. Sending is a separate action. Earlier urgent flags remain in history.')
            self.render_steps()
            self.show_stage('guidance', focus=True)
            self.refresh_host()
            self.check_context()
        except (ValueError, OSError, TypeError) as exc:
            self.status.configure(text='Assessment not recorded: ' + str(exc))
            self.check_context()

    def render_steps(self):
        for widget in self.steps_frame.winfo_children():
            widget.destroy()
        self.action_widgets = []
        if self.saved is None:
            self.action_note_button.pack_forget()
            self.action_note_frame.pack_forget()
            self.action_notes_open = False
            self.action_note_button.configure(text='Add completion note')
            return
        records = self.controller.assistant_records('crew')
        completed = {r['step_id'] for r in records if r.get('kind') == 'action' and r.get('assessment_id') == self.saved['id']}
        ttk.Label(self.steps_frame, text=f'{len(completed)} of {len(self.saved["steps"])} actions recorded',
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(self.steps_frame, text='Completed actions do not mean symptoms have resolved.',
                  foreground=MUTED, wraplength=490).pack(fill='x', pady=(3, 0))
        self.action_note_button.pack(anchor='w', before=self.reassess_button, pady=(10, 0))
        for step in self.saved['steps']:
            done = step['id'] in completed
            ttk.Label(self.steps_frame, text=('Completed · ' if done else 'Pending · ') + step['text'], wraplength=690).pack(fill='x', pady=(8, 2))
            if done:
                continue
            row = ttk.Frame(self.steps_frame)
            row.pack(fill='x')
            button = ttk.Button(row, text='Record completion', command=lambda s=step: self.complete(s))
            button.pack(side='left')
            self.explain(button, 'Record that you performed this step. This does not confirm symptom resolution.')
            self.action_widgets.append(button)
            tool = step.get('tool')
            if tool in self.tools:
                button = ttk.Button(row, text={'manual': 'Open measurements', 'reaction': 'Check reaction time', 'signal': 'Review signal'}[tool],
                                    command=lambda t=tool: self.open_tool(t))
                button.pack(side='left', padx=5)
                self.action_widgets.append(button)
        width = max(260, self.canvas.winfo_width() - 12)
        for widget in self.steps_frame.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=width)

    def complete(self, step):
        if not self.check_context() or self.saved is None:
            return
        try:
            self.controller.complete_assistant(self.saved['id'], step['id'], self.action_note.get(),
                                               expected_context=self.context)
            self.context = self.controller.assistant_context()
            self.history_refresh_at = 0.0
            self.action_note.delete(0, 'end')
            self.render_steps()
            self.refresh_host()
            self.status.configure(text='Explicit action recorded. This does not resolve symptoms or provide clinical clearance. Reassess after new evidence.')
            self.check_context()
        except (ValueError, OSError, TypeError) as exc:
            self.status.configure(text='Action not recorded: ' + str(exc))
            self.check_context()

    def open_tool(self, tool):
        if self.check_context():
            self.tools[tool]()
            self.status.configure(text='Acquisition tool opened. New evidence locks this plan; use Fresh reassessment afterwards.')

    def reassess(self):
        if not self.same_session():
            self.check_context()
            return
        self.context = self.controller.assistant_context()
        self.history_refresh_at = 0.0
        self.stale = False
        self._reset_answers()
        self.note.configure(state='normal')
        self.note.delete('1.0', 'end')
        self.action_note.delete(0, 'end')
        self.render_steps()
        self.update_question()
        self.show_stage('questions', focus=True)
        self.status.configure(text='Fresh intake against current evidence. Previous records and urgent flags stay in history.')

    def send(self):
        if not self.check_context():
            return
        try:
            self.controller.send_consultation()
            self.refresh_host()
            self.status.configure(text='Consultation queued on the simulated delayed link. This is not emergency transmission or actual medical help.')
            self.check_context()
        except (ValueError, OSError, TypeError) as exc:
            self.status.configure(text='Consultation not sent: ' + str(exc))

    def advance(self):
        if self.stale or not self.same_session():
            self.check_context()
            return
        try:
            self.controller.advance()
            self.refresh_host()
            self.check_context()
            self.status.configure(text='Simulated delivery clock advanced. Earth handoff contains delivered snapshots only.')
        except (ValueError, OSError, TypeError) as exc:
            self.status.configure(text='Link not advanced: ' + str(exc))

    def handoff(self, role):
        if not self.same_session():
            self.status.configure(text='Crew/session changed. Close and reopen the assistant to view that context.')
            return
        try:
            packet = self.controller.assistant_handoff(role)
            self.show_text('Crew local history / handoff' if role == 'crew' else 'Earth delivered handoff only', packet['text'])
        except (ValueError, OSError, TypeError) as exc:
            self.status.configure(text='Handoff unavailable: ' + str(exc))

    def show_details(self):
        sections = [str(self.catalog['disclaimer']),
            'Mission routing is a project adaptation, not a NASA-approved protocol. NASA context and experimental signal-review models do not determine symptom urgency. No clinician review or clinical validation.',
            'Answer choices are explicit; missing answers stay unknown. Notes are retained verbatim and not interpreted. Completed actions do not close prior urgent flags.']
        for source in self.catalog['sources']:
            sections.append('\n' + source['title'] + '\n' + source['url'] +
                '\nAccessed: ' + source['accessed_at'] + ' | Role: ' + source['role'] +
                '\nClinical review: ' + source['clinical_review_status'] +
                ('\nSource review due: ' + str(source['review_due']) +
                 (' · past due as of source access' if str(source['review_due']) < source['accessed_at'][:10] else '')
                 if source.get('review_due') else ''))
        self.show_text('Assistant details / source references', '\n\n'.join(sections))

    def show_text(self, title, text):
        child = tk.Toplevel(self.window)
        child.configure(bg=BG)
        child.title(title)
        child.geometry('720x530')
        child.minsize(480, 320)
        box = scrolledtext.ScrolledText(child, wrap='word', bg=PLOT, fg=INK,
                                       font=('Segoe UI', 10), padx=14, pady=14)
        box.pack(fill='both', expand=True)
        _text(box, text)
        child.bind('<Escape>', lambda _: child.destroy())
        ttk.Button(child, text='Close', command=child.destroy).pack(pady=8)
        return child

    def close(self):
        self._close(destroy=True)

    def _window_destroyed(self, event):
        if event.widget is self.window:
            self._close(destroy=False)

    def _close(self, *, destroy):
        if self.closed:
            return
        self.closed = True
        if self.job is not None:
            self.window.after_cancel(self.job)
            self.job = None
        if destroy and self.window.winfo_exists():
            self.window.destroy()
        on_close = self.on_close
        self.on_close = None
        self.refresh_host = None
        self.start_local = None
        self.tools = {}
        # A retained closed draft must not keep the host, Tk variables or the
        # interpreter alive until cyclic collection on an unrelated worker.
        self.answer_buttons = []
        self.action_widgets = []
        for name, value in tuple(vars(self).items()):
            if isinstance(value, (tk.Misc, tk.Variable)):
                setattr(self, name, None)
        if on_close:
            on_close(self)
