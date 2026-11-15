"""Closed UI owners must be safe when later garbage is collected on a worker."""
import gc
import queue
import sys
import threading
import tkinter as tk
import unittest
import warnings
import weakref
from unittest.mock import patch

from biotwin.assistant_ui import AssistantDialog
from biotwin.desktop import DesktopApp
from biotwin.desktop_controller import DesktopController


class TkLifecycleTests(unittest.TestCase):
    def collect_on_worker(self, owners):
        """Drop a real ownership cycle on a worker without disabling GC."""
        failures = []
        owner_refs = [weakref.ref(owner) for owner in owners]
        owners.append(owners)
        inbox = queue.SimpleQueue()
        inbox.put(owners)
        del owners

        def collect():
            garbage = inbox.get()
            del garbage
            gc.collect()

        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter('always')
            with patch.object(sys, 'unraisablehook', failures.append):
                worker = threading.Thread(target=collect)
                worker.start()
                worker.join(10)
        self.assertFalse(worker.is_alive(), 'Tk cleanup blocked on a background thread')
        self.assertFalse(failures, [str(f.exc_value) for f in failures])
        self.assertFalse(emitted, [str(item.message) for item in emitted])
        self.assertTrue(all(ref() is None for ref in owner_refs), 'Closed owner cycle was not collected')

    def test_dialog_close_releases_variables_callbacks_children_and_timer(self):
        def exercise(native_destroy):
            root = tk.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *args: errors.append(args)
            controller = DesktopController()
            controller.start_local()
            closed = []
            dialog = AssistantDialog(root, controller, on_close=closed.append)
            dialog.window.withdraw()
            detail = dialog.show_text('Lifecycle test', 'No medical content.')
            detail.withdraw()
            root.update_idletasks()
            job = dialog.job
            variable_ref = weakref.ref(dialog.answer)
            root_ref = weakref.ref(root)
            if native_destroy:
                dialog.window.destroy()
            else:
                dialog.close()
            dialog.close()
            self.assertTrue(dialog.closed)
            self.assertEqual(closed, [dialog])
            self.assertIsNone(variable_ref())
            self.assertNotIn(job, root.tk.call('after', 'info'))
            self.assertFalse(root.winfo_children())
            self.assertIsNone(dialog.refresh_host)
            self.assertIsNone(dialog.on_close)
            self.assertFalse(dialog.tools)
            self.assertFalse(errors)
            root.destroy()
            del root, detail, closed
            self.assertIsNone(root_ref(), 'Closed draft retained its Tk root')
            return [dialog]

        for native_destroy in (False, True):
            with self.subTest(native_destroy=native_destroy):
                self.collect_on_worker(exercise(native_destroy))

    def test_app_close_releases_gui_even_when_closed_owners_are_retained(self):
        self.collect_on_worker(self.closed_app_owners())

    def closed_app_owners(self):
        root = tk.Tk()
        root.withdraw()
        errors = []
        root.report_callback_exception = lambda *args: errors.append(args)
        controller = DesktopController()
        controller.start_local()
        with patch.object(DesktopApp, 'discover_references'):
            app = DesktopApp(root, controller=controller, startup=False)
        dialog = app.onboard_assistant()
        dialog.window.withdraw()
        dialog.show_text('Lifecycle test', 'Detached detail window.').withdraw()
        original_toplevel = tk.Toplevel

        def hidden(*args, **kwargs):
            window = original_toplevel(*args, **kwargs)
            window.withdraw()
            return window

        with patch('biotwin.desktop.tk.Toplevel', new=hidden):
            app.reaction_dialog()
        self.assertTrue(app.local_cancels)
        root.update_idletasks()
        # Let several real timer callbacks reschedule before cancelling them.
        root.after(40, root.quit)
        root.mainloop()
        variable_refs = [weakref.ref(variable) for variable in
                         (app.status, app.crew_var, app.consult_position, dialog.answer,
                          *(panel['position'] for panel in app.signal_panels.values()))]
        root_ref = weakref.ref(root)
        app.close()
        app.close()
        self.assertTrue(dialog.closed)
        self.assertFalse(app.assistant_dialogs)
        self.assertFalse(app.local_windows)
        self.assertFalse(app.local_cancels)
        self.assertTrue(all(ref() is None for ref in variable_refs))
        self.assertFalse(root.tk.call('after', 'info'))
        self.assertFalse(errors)
        del root
        self.assertIsNone(root_ref(), 'Closed app retained its Tk root')
        return [app, dialog]

    def test_reference_worker_does_not_retain_closed_application(self):
        root = tk.Tk()
        root.withdraw()
        with patch.object(DesktopApp, 'discover_references'):
            app = DesktopApp(root, startup=False)
        started, release = threading.Event(), threading.Event()

        def load():
            started.set()
            release.wait(10)
            return {'cards': []}

        result_queue = app.load_queue
        app.start_reference_work(load, 'Bounded lifecycle test')
        self.assertTrue(started.wait(5))
        app_ref, root_ref = weakref.ref(app), weakref.ref(root)
        app.close()
        del app, root
        try:
            self.assertIsNone(app_ref(), 'Loader retained DesktopApp')
            self.assertIsNone(root_ref(), 'Loader retained the Tk interpreter')
            # Collection happens while the real loader is still in flight.
            self.collect_on_worker([])
        finally:
            release.set()
            self.assertEqual(result_queue.get(timeout=5), ('okay', {'cards': []}))

    def test_retained_intro_releases_root_and_animation_on_app_close(self):
        root=tk.Tk(); root.withdraw()
        with patch.object(DesktopApp,'discover_references'):
            app=DesktopApp(root,startup=False)
        tour=app.show_introduction(animate=True); tour.window.withdraw()
        root_ref=weakref.ref(root); job=tour.job
        app.close()
        self.assertIsNone(tour.root)
        self.assertNotIn(job,root.tk.call('after','info'))
        del root
        self.assertIsNone(root_ref())
        owners=[app,tour]
        del app,tour
        self.collect_on_worker([owners.pop(),owners.pop()])


if __name__ == '__main__':
    unittest.main()
