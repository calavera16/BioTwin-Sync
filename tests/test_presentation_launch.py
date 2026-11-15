import tkinter as tk
import unittest
from unittest.mock import patch

from biotwin.desktop import DesktopApp, main
from biotwin.desktop_controller import CREWS, DesktopController
from biotwin.presentation import PresentationHost
from tests.test_presentation import AssetsFixture


class PresentationLaunchTests(unittest.TestCase):
    def test_native_launch_reuses_host_preserves_session_and_stops_thread(self):
        with AssetsFixture() as directory:
            native = DesktopController()
            native.start_local()
            before = native.document()
            root = tk.Tk()
            root.withdraw()
            app = DesktopApp(root, controller=native)
            host = PresentationHost(directory=directory)
            try:
                with patch('biotwin.presentation.PresentationHost', return_value=host), patch('webbrowser.open') as opened:
                    self.assertEqual(app.presentation_button.cget('text'), 'Presentation')
                    self.assertIs(app.open_presentation(), host)
                    thread = host.thread
                    self.assertIs(app.open_presentation(), host)
                    self.assertEqual(opened.call_count, 2)
                    opened.assert_called_with(host.url, new=2)
                host.controller.action({'role': 'crew', 'crew': CREWS[0], 'action': 'seek',
                    'expected_revision': 0, 'payload': {'seconds': 2400}})
                self.assertEqual(native.document(), before)
                self.assertEqual(host.controller.state('crew', CREWS[0])['projection']['metric']['value'], 585)
                app.close()
                app.close()
                self.assertFalse(thread.is_alive())
                self.assertIsNone(app.presentation_host)
                self.assertIsNone(app.open_presentation(open_browser=False))
            finally:
                if not app.closed: app.close()
                host.close()

    def test_missing_assets_report_start_failure_without_launching_browser(self):
        with AssetsFixture() as directory:
            (directory/'app.js').unlink()
            root = tk.Tk()
            root.withdraw()
            app = DesktopApp(root)
            host = PresentationHost(directory=directory)
            try:
                with patch('biotwin.presentation.PresentationHost', return_value=host), patch('webbrowser.open') as opened:
                    self.assertIsNone(app.open_presentation())
                    opened.assert_not_called()
                self.assertIsNone(app.presentation_host)
                self.assertIsNone(host.thread)
                self.assertIn('could not start', app.status.get())
            finally:
                app.close()

    def test_browser_failure_keeps_host_ready_for_retry(self):
        with AssetsFixture() as directory:
            root = tk.Tk()
            root.withdraw()
            app = DesktopApp(root)
            host = PresentationHost(directory=directory)
            try:
                with patch('biotwin.presentation.PresentationHost', return_value=host), patch('webbrowser.open', return_value=False):
                    self.assertIs(app.open_presentation(), host)
                    thread = host.thread
                    self.assertTrue(thread.is_alive())
                    self.assertIn('no browser opened', app.status.get())
                    self.assertIn('Windows Settings', app.status.get())
                with patch('webbrowser.open', return_value=True) as opened:
                    self.assertIs(app.open_presentation(), host)
                    self.assertIs(host.thread, thread)
                    opened.assert_called_once_with(host.url, new=2)
                    self.assertIn('Presentation is open', app.status.get())
            finally:
                app.close()

    def test_presentation_entry_schedules_launch_without_native_onboarding(self):
        with patch('biotwin.desktop.tk.Tk') as constructor, patch('biotwin.desktop.DesktopApp') as app:
            self.assertEqual(main(['--presentation']), 0)
            app.assert_called_once_with(constructor.return_value, startup=False)
            constructor.return_value.after_idle.assert_called_once_with(app.return_value.open_presentation)
            constructor.return_value.mainloop.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
