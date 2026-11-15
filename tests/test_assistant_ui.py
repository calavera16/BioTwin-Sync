"""Actual withdrawn-Tk behavior checks for the offline assistant."""
import tempfile
import tkinter as tk
from unittest import TestCase
from unittest.mock import patch

from biotwin.assistant_ui import AssistantDialog
from biotwin.desktop import DesktopApp
from biotwin.desktop_controller import DesktopController, CREWS


class AssistantUITests(TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args, errors=self.errors: errors.append(args)
        self.controller = DesktopController()
        self.controller.start_local()
        self.dialogs = []
        self.original_toplevel = tk.Toplevel
        self.app = None

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
        if self.app is not None:
            self.app.close()
        else:
            self.root.destroy()
        self.assertFalse(self.errors)

    def hidden(self, *args, **kwargs):
        window = self.original_toplevel(*args, **kwargs)
        window.withdraw()
        return window

    def dialog(self, **kwargs):
        with patch('biotwin.assistant_ui.tk.Toplevel', new=self.hidden):
            dialog = AssistantDialog(self.root, self.controller, **kwargs)
        self.dialogs.append(dialog)
        self.root.update_idletasks()
        return dialog

    def answer_all_no(self, dialog):
        for i, question in enumerate(dialog.protocol['questions']):
            dialog.index = i
            dialog.update_question()
            dialog.answer.set('no')
            dialog.answer_changed()

    def test_three_paths_unknown_defaults_and_live_urgent_before_save(self):
        dialog = self.dialog()
        self.assertEqual(len(dialog.protocols), 3)
        for index in range(3):
            dialog.choice.current(index)
            dialog.change_protocol()
            self.assertTrue(all(value == 'unsure' for value in dialog.answers.values()))
            self.assertEqual(dialog.preview['status'], 'clarify')
            self.assertIn('Unknown', dialog.immediate.cget('text'))
        urgent_index = next(i for i, q in enumerate(dialog.protocol['questions']) if q['kind'] == 'urgent')
        dialog.index = urgent_index
        dialog.update_question()
        dialog.answer.set('yes')
        dialog.answer_changed()
        self.assertEqual(dialog.preview['status'], 'urgent')
        self.assertIn('URGENT', dialog.immediate.cget('text'))
        self.assertIn('Do not wait', dialog.immediate.cget('text'))
        self.assertFalse(self.controller.assistant_records('crew'))
        dialog.note.insert('1.0', 'x' * 1001)
        dialog.update_preview()
        self.assertEqual(dialog.preview['status'], 'urgent')
        self.assertIn('URGENT', dialog.immediate.cget('text'))
        self.assertEqual(str(dialog.record_button['state']), 'disabled')

    def test_persistent_urgent_help_and_source_details_do_not_save_or_send(self):
        dialog = self.dialog()
        with patch.object(dialog, 'show_text') as show:
            dialog.urgent_help()
            self.assertIn('No alert is transmitted', show.call_args.args[1])
            dialog.show_details()
            details = show.call_args.args[1]
            self.assertIn('not_clinician_reviewed', details)
            self.assertIn('https://www.nhs.uk', details)
            self.assertIn('past due', details)
        self.assertFalse(self.controller.assistant_records('crew'))
        self.assertFalse(self.controller.assistant_records('doctor'))

    def test_record_explicit_action_reassess_roundtrip_and_delayed_handoff(self):
        dialog = self.dialog()
        self.answer_all_no(dialog)
        note = 'Verbatim user note: unclear symptoms; not interpreted.'
        dialog.note.insert('1.0', note)
        dialog.update_preview()
        dialog.record()
        self.assertIsNotNone(dialog.saved, dialog.status.cget('text'))
        assessment = dialog.saved
        self.assertEqual(assessment['note'], note)
        self.assertFalse(self.controller.assistant_records('doctor'))
        step = assessment['steps'][0]
        dialog.action_note.insert(0, 'Explicit QA action completion, not symptom resolution.')
        dialog.complete(step)
        records = self.controller.assistant_records('crew')
        self.assertEqual(len([r for r in records if r['kind'] == 'action']), 1, dialog.status.cget('text'))
        self.assertIn('does not resolve', dialog.status.cget('text'))
        dialog.send()
        self.assertFalse(self.controller.assistant_records('doctor'))
        dialog.advance()
        delivered = self.controller.assistant_records('doctor')
        self.assertEqual(len(delivered), 2)
        with patch.object(dialog, 'show_text') as show:
            dialog.handoff('doctor')
            self.assertIn(note, show.call_args.args[1])
        dialog.reassess()
        self.assertIsNone(dialog.saved)
        self.assertTrue(all(v == 'unsure' for v in dialog.answers.values()))
        dialog.record()
        self.assertEqual(len(self.controller.assistant_records('crew')), 3)
        self.assertEqual(len(self.controller.assistant_records('doctor')), 2)
        with tempfile.TemporaryDirectory() as folder:
            expected = self.controller.document()
            path = self.controller.save(folder)
            self.controller.open(path)
            self.assertEqual(self.controller.document(), expected)
        self.assertFalse(dialog.check_context())
        self.assertTrue(dialog.stale)

    def test_switch_and_replaced_session_cannot_write_or_read_other_crew(self):
        dialog = self.dialog()
        before = self.controller.document()
        self.controller.select(CREWS[1])
        self.assertFalse(dialog.check_context())
        dialog.record()
        with patch.object(dialog, 'show_text') as show:
            dialog.handoff('crew')
            show.assert_not_called()
        self.controller.select(CREWS[0])
        dialog.record()
        self.assertEqual(self.controller.document(), before)
        self.controller.start_local()
        replacement = self.controller.document()
        dialog.reassess()
        dialog.record()
        self.assertEqual(self.controller.document(), replacement)
        self.assertTrue(dialog.stale)

    def test_new_evidence_locks_actions_until_fresh_intake(self):
        from datetime import datetime, timezone
        dialog = self.dialog()
        self.answer_all_no(dialog)
        dialog.record()
        step = dialog.saved['steps'][0]
        self.controller.capture_manual({'subject_id': self.controller.subject,
            'metric': 'heart_rate', 'value': 72, 'unit': 'bpm',
            'observed_at': datetime.now(timezone.utc).isoformat(), 'source_id': 'ui-QA-new-source',
            'method': 'synthetic QA entry', 'quality': 'review'})
        self.assertFalse(dialog.check_context())
        self.assertEqual(str(dialog.record_button['state']), 'disabled')
        before = self.controller.document()
        dialog.action_note.insert(0, 'Must not record against stale plan')
        dialog.complete(step)
        self.assertEqual(before, self.controller.document())
        dialog.reassess()
        self.assertFalse(dialog.stale)
        self.assertTrue(dialog.check_context())

    def test_shortcuts_require_context_and_match_existing_tool(self):
        from unittest.mock import Mock
        tools = {name: Mock() for name in ('manual', 'reaction', 'signal')}
        dialog = self.dialog(tools=tools)
        for name in tools:
            dialog.open_tool(name)
            tools[name].assert_called_once()
        self.controller.start_local()
        for name in tools:
            dialog.open_tool(name)
            tools[name].assert_called_once()

    def test_prior_urgent_stays_visible_through_fresh_negative_intake(self):
        dialog = self.dialog()
        dialog.answers[next(q['id'] for q in dialog.protocol['questions'] if q['kind'] == 'urgent')] = 'yes'
        dialog.update_preview()
        dialog.record()
        self.assertIn('1 prior urgent', dialog.urgent_history.cget('text'))
        dialog.reassess()
        self.answer_all_no(dialog)
        self.assertIn('1 prior urgent', dialog.urgent_history.cget('text'))
        dialog.record()
        self.assertIn('1 prior urgent', dialog.urgent_history.cget('text'))
        self.assertIn('do not resolve', dialog.urgent_history.cget('text'))

    def test_idle_wall_expiry_locks_plan_without_new_evidence(self):
        from datetime import datetime, timedelta, timezone
        dialog = self.dialog()
        self.answer_all_no(dialog)
        dialog.record()
        context = self.controller.assistant_context()
        later = datetime.now(timezone.utc) + timedelta(minutes=31)
        dialog.history_refresh_at = 0.0
        with patch('biotwin.desktop_controller.datetime', wraps=datetime) as mocked:
            mocked.now.return_value = later
            self.assertFalse(dialog.check_context())
        self.assertEqual(context, self.controller.assistant_context())
        self.assertTrue(dialog.stale)
        self.assertIn('not a medically safe waiting time', dialog.context_label.cget('text'))

    def test_scroll_and_fixed_controls_fit_minimum_width(self):
        from types import SimpleNamespace
        dialog = self.dialog()
        self.root.update_idletasks()
        dialog.resize(SimpleNamespace(width=526))
        self.assertLessEqual(dialog.send_button.master.winfo_reqwidth(), 570)
        self.assertIsNot(dialog.immediate.master, dialog.body)
        self.assertIsNot(dialog.urgent_history.master, dialog.body)
        self.assertTrue(dialog.canvas.cget('yscrollcommand'))
        self.assertTrue(dialog.canvas.bind('<Next>'))
        self.assertTrue(dialog.window.bind('<FocusIn>'))
        self.assertEqual(int(float(dialog.status.cget('wraplength'))), 514)

    def test_two_stages_keep_one_task_visible_and_review_does_not_save(self):
        dialog = self.dialog()
        self.assertEqual(dialog.stage, 'questions')
        self.assertEqual(dialog.questions_frame.winfo_manager(), 'pack')
        self.assertFalse(dialog.guidance_frame.winfo_manager())
        self.assertFalse(dialog.record_button.winfo_manager())
        self.assertFalse(dialog.send_button.winfo_manager())
        for _ in range(len(dialog.protocol['questions']) - 1):
            dialog.next.invoke()
        self.assertEqual(dialog.next.cget('text'), 'Review guidance')
        dialog.next.invoke()
        self.assertEqual(dialog.stage, 'guidance')
        self.assertFalse(dialog.questions_frame.winfo_manager())
        self.assertEqual(dialog.guidance_frame.winfo_manager(), 'pack')
        self.assertEqual(dialog.record_button.winfo_manager(), 'pack')
        self.assertTrue(all(v == 'unsure' for v in dialog.answers.values()))
        self.assertFalse(self.controller.assistant_records('crew'))
        dialog.record_button.invoke()
        self.assertIsNotNone(dialog.saved)
        self.assertFalse(dialog.record_button.winfo_manager())
        self.assertEqual(dialog.send_button.winfo_manager(), 'pack')
        dialog.reassess_button.invoke()
        self.assertEqual(dialog.stage, 'questions')
        self.assertIsNone(dialog.saved)
        self.assertEqual(len(self.controller.assistant_records('crew')), 1)

    def test_disclosures_preserve_notes_and_keep_urgent_preview_outside_stages(self):
        dialog = self.dialog()
        self.assertFalse(dialog.note_frame.winfo_manager())
        self.assertFalse(dialog.link_frame.winfo_manager())
        dialog.note_button.invoke()
        dialog.note.insert('1.0', 'Retain this literal note')
        dialog.note_button.invoke()
        self.assertEqual(dialog.note.get('1.0', 'end-1c'), 'Retain this literal note')
        urgent = next(q['id'] for q in dialog.protocol['questions'] if q['kind'] == 'urgent')
        dialog.answers[urgent] = 'yes'
        dialog.update_preview()
        for stage in ('guidance', 'questions'):
            dialog.show_stage(stage)
            self.assertEqual(dialog.immediate.master, dialog.header)
            self.assertEqual(dialog.immediate.winfo_manager(), 'pack')
            self.assertIn('URGENT', dialog.immediate.cget('text'))
            self.assertEqual(dialog.urgent_button.winfo_manager(), 'pack')
        self.assertFalse(self.controller.assistant_records('crew'))
        dialog.toggle_link()
        self.assertEqual(dialog.link_frame.winfo_manager(), 'pack')
        dialog.show_stage('guidance')
        self.assertEqual(dialog.body.pack_slaves()[-1], dialog.link_frame)
        dialog.toggle_link()
        self.assertFalse(dialog.link_frame.winfo_manager())
        dialog.record()
        self.assertEqual(dialog.saved['note'], 'Retain this literal note')
        self.assertFalse(dialog.action_note_frame.winfo_manager())
        dialog.action_note_button.invoke()
        dialog.action_note.insert(0, 'Literal completion note')
        dialog.action_note_button.invoke()
        dialog.complete(dialog.saved['steps'][0])
        self.assertEqual(self.controller.assistant_records('crew')[-1]['note'], 'Literal completion note')
        self.assertEqual(dialog.urgent_history.winfo_manager(), 'pack')

    def test_keyboard_and_pointer_help_match_and_close_cancels_only_own_timer(self):
        baseline = set(self.root.tk.call('after', 'info'))
        for _ in range(3):
            dialog = self.dialog()
            dialog.record_button.event_generate('<Enter>')
            pointer_help = dialog.help_label.cget('text')
            dialog.record_button.event_generate('<Leave>')
            dialog.record_button.event_generate('<FocusIn>')
            self.assertEqual(dialog.help_label.cget('text'), pointer_help)
            self.assertIn('Does not send', pointer_help)
            job = dialog.job
            self.assertIn(job, self.root.tk.call('after', 'info'))
            dialog.close()
            self.assertIsNone(dialog.job)
            self.assertIsNone(dialog.answer)
            self.assertIsNone(dialog.more_menu)
            self.assertNotIn(job, self.root.tk.call('after', 'info'))
        self.assertEqual(set(self.root.tk.call('after', 'info')), baseline)

    def test_footer_is_reserved_and_safety_is_fixed_at_supported_sizes(self):
        from types import SimpleNamespace
        dialog = self.dialog()
        for width, height in ((570, 520), (760, 720)):
            dialog.window.geometry(f'{width}x{height}')
            dialog.resize(SimpleNamespace(width=width - 44))
            self.root.update_idletasks()
            self.assertEqual(dialog.footer.pack_info()['side'], 'bottom')
            self.assertLess(dialog.window.pack_slaves().index(dialog.footer),
                            dialog.window.pack_slaves().index(dialog.canvas.master))
            self.assertLessEqual(dialog.footer.winfo_reqwidth(), width)
            self.assertLess(dialog.header.winfo_reqheight() + dialog.footer.winfo_reqheight(), height - 80)
            for stage in ('questions', 'guidance'):
                dialog.show_stage(stage)
                self.assertEqual(dialog.immediate.winfo_manager(), 'pack')
                self.assertEqual(dialog.footer.winfo_manager(), 'pack')
        # Even the longest safety state leaves a scrollable task area at minimum size.
        dialog.resize(SimpleNamespace(width=526))
        dialog.show_stage('questions')
        dialog.answer.set('yes')
        dialog.answer_changed()
        self.root.update_idletasks()
        # First urgent question and all answer choices fit before scrolling.
        def packed_height(widget):
            padding = widget.pack_info()['pady']
            padding = sum(map(int, padding)) if isinstance(padding, tuple) else 2 * int(padding)
            return widget.winfo_reqheight() + padding
        question_height = packed_height(dialog.questions_button.master)
        for widget in dialog.questions_frame.pack_slaves():
            question_height += packed_height(widget)
            if widget is dialog.answer_buttons[0].master:
                break
        self.assertLess(dialog.header.winfo_reqheight() + dialog.footer.winfo_reqheight() + question_height, 520)
        for question in dialog.protocol['questions']:
            dialog.answers[question['id']] = 'yes'
        dialog.update_preview()
        dialog.record()
        dialog.stale = True
        dialog.check_context()
        self.root.update_idletasks()
        self.assertLess(dialog.header.winfo_reqheight() + dialog.footer.winfo_reqheight(), 440)
        self.assertIn('replaces workspace', dialog.begin_button.cget('text'))

    def test_desktop_entry_and_shutdown_keep_old_buttons_working(self):
        with patch('biotwin.desktop.DesktopApp.discover_references'):
            app = DesktopApp(self.root, controller=self.controller)
        self.app = app
        try:
            with patch('biotwin.assistant_ui.tk.Toplevel', new=self.hidden):
                dialog = app.onboard_assistant()
            self.assertIn(dialog, app.assistant_dialogs)
            self.root.update()
            app.open_monitor(CREWS[1])
            self.assertTrue(dialog.stale)
            app.open_monitor(CREWS[0])
            self.assertEqual(app.screen, 'monitor')
            app.show_home()
            self.assertEqual(app.screen, 'home')
            dialog.close()
            self.assertFalse(app.assistant_dialogs)
        finally:
            app.close()

    def test_assistant_only_delivered_record_is_not_a_scalar_condition(self):
        dialog = self.dialog()
        dialog.record()
        dialog.send()
        dialog.advance()
        with patch('biotwin.desktop.DesktopApp.discover_references'):
            app = DesktopApp(self.root, controller=self.controller)
        self.app = app
        try:
            with patch.object(app, 'show_text') as show:
                app.author_dialog()
            self.assertIn('No delivered scalar', show.call_args.args[0])
            self.assertIn('not numeric', show.call_args.args[1])
        finally:
            app.close()
