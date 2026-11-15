"""Deterministic Tk geometry and keyboard behavior; no visual acceptance claim."""
import copy
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk, scrolledtext
import unittest
from unittest.mock import Mock, patch

from biotwin.desktop import DesktopApp
from biotwin.desktop_theme import SELECTED
from biotwin.desktop_ux import Introduction, ScrollableForm, form_text_navigation


class DesktopPolishTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk(); self.root.withdraw()
        self.errors=[]
        self.root.report_callback_exception=lambda *args:self.errors.append(args)
        with patch.object(DesktopApp,'discover_references'):
            self.app=DesktopApp(self.root,startup=False)

    def tearDown(self):
        self.app.close()
        self.assertFalse(self.errors)

    def settle(self):
        for _ in range(4): self.root.update()

    def map_hidden(self, window, geometry):
        self.root.attributes('-alpha',0.0)
        self.root.geometry('1050x720+20000+20000'); self.root.deiconify()
        window.attributes('-alpha',0.0)
        window.geometry(geometry+'+20000+20000'); window.deiconify(); self.settle()

    def descendants(self, parent):
        for child in parent.winfo_children():
            yield child
            yield from self.descendants(child)

    def test_author_form_small_window_keeps_actions_and_reveals_last_field(self):
        a=self.app
        a.controller.reset(interactive=True); a.controller.capture(585)
        a.controller.send_consultation(); a.controller.advance(); a.refresh()
        before=copy.deepcopy(a.controller.document())
        window=a.author_dialog()
        self.map_hidden(window,'560x480')
        children=list(self.descendants(window))
        form=next(w for w in children if isinstance(w,ScrollableForm))
        fields=[w for w in children if isinstance(w,scrolledtext.ScrolledText)]
        self.assertEqual(len(fields),3)
        self.assertLess(form.canvas.yview()[1],1)
        for text in ('Send authored checklist','Cancel'):
            button=next(w for w in children if isinstance(w,ttk.Button) and w.cget('text')==text)
            self.assertTrue(button.winfo_ismapped())
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),window.winfo_rooty()+window.winfo_height())
        last=fields[-1]
        form.reveal(SimpleNamespace(widget=last)); self.settle()
        self.assertGreater(form.canvas.yview()[0],0)
        last.insert('end','\n'+'A long source-preserving step. '*300)
        before_text=last.get('1.0','end-1c')
        # Exercise configured callbacks without requiring OS keyboard focus.
        self.assertTrue(last.bind('<Tab>')); self.assertTrue(last.bind('<Shift-Tab>'))
        with patch.object(last,'bind') as bind:
            form_text_navigation(last)
        callbacks={call.args[0]:call.args[1] for call in bind.call_args_list}
        for sequence,method in (('<Tab>','tk_focusNext'),('<Shift-Tab>','tk_focusPrev')):
            target=Mock()
            with patch.object(last,method,return_value=target):
                self.assertEqual(callbacks[sequence](None),'break')
                target.focus_set.assert_called_once()
        self.assertEqual(last.get('1.0','end-1c'),before_text)
        window.destroy(); self.settle()
        self.assertEqual(a.controller.document(),before)

    def test_quick_guide_long_reference_preserves_text_and_navigation_at_minimum(self):
        tour=Introduction(self.root,facts={'reference':'Earlier comparable observations describe sample spread, never health clearance. '*5})
        self.map_hidden(tour.window,'560x480'); tour.forward(); self.settle()
        for widget in (tour.heading,tour.explanation,tour.next,tour.back,tour.skip):
            self.assertTrue(widget.winfo_ismapped())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),tour.window.winfo_rooty()+tour.window.winfo_height())
        self.assertGreater(tour.explanation.winfo_height(),50)
        tour.close()

    def test_checklist_preserves_selection_on_completion_refresh_and_scrolls_only_when_needed(self):
        a=self.app; a.jump(3)
        self.map_hidden(a.checklist_window,'640x530')
        a.selected_step_index=1
        # Rebuild the rows as a completion update does.
        a.step_signature=None; a.refresh_guide(); self.settle()
        self.assertEqual(a.step_widgets[1].cget('background'),SELECTED)
        a.steps_canvas.yview_moveto(0); self.settle()
        top=a.steps_canvas.yview()
        a.reveal_step(0)
        self.assertEqual(a.steps_canvas.yview(),top)
        with patch.object(a.step_widgets[1],'focus_set') as focus:
            self.assertEqual(a.focus_step(1),'break'); focus.assert_called_once()

    def test_browser_failure_exposes_address_only_in_recovery_and_retry_reuses_host(self):
        a=self.app; before=copy.deepcopy(a.controller.document())
        host=Mock(url='http://127.0.0.1:12345/#test-only')
        with patch('biotwin.presentation.PresentationHost',return_value=host),patch('webbrowser.open',return_value=False):
            self.assertIs(a.open_presentation(),host)
            window=a.presentation_recovery
            self.assertNotIn(host.url,a.status.get())
            a.open_presentation(); self.assertIs(a.presentation_recovery,window)
            host.start.assert_called_once()
        children=list(self.descendants(window))
        address=next(w for w in children if isinstance(w,ttk.Entry))
        self.assertEqual(address.get(),host.url)
        copy_button=next(w for w in children if isinstance(w,ttk.Button) and w.cget('text')=='Copy local address')
        with patch.object(window,'clipboard_clear') as clear,patch.object(window,'clipboard_append') as append:
            copy_button.invoke(); clear.assert_called_once(); append.assert_called_once_with(host.url)
        with patch('webbrowser.open',return_value=True): a.open_presentation()
        self.assertIsNone(a.presentation_recovery)
        self.assertEqual(a.controller.document(),before)


if __name__=='__main__': unittest.main()
