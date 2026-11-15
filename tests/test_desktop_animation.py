import unittest
from unittest.mock import patch
import tkinter as tk

from biotwin.desktop_animation import Playback, RecordTrace, packet_progress
from biotwin.desktop_assets import load_examples
from biotwin.desktop_controller import DesktopController


class AnimationTests(unittest.TestCase):
    def test_elapsed_clock_is_independent_of_frame_count(self):
        a, b = Playback(60), Playback(60)
        a.play(10)
        b.play(10)
        for now in (10.03, 10.08, 10.41, 11.0):
            a.sample(now)
        self.assertAlmostEqual(a.position, b.sample(11), places=10)
        a.pause(12)
        self.assertEqual(a.sample(40), 2)
        a.play(50)
        self.assertEqual(a.sample(51), 3)

    def test_speed_change_seek_and_end_are_deterministic(self):
        clock = Playback(10)
        clock.play(0)
        clock.set_speed(2, 2)
        self.assertEqual(clock.sample(4), 6)
        clock.seek(1, 5)
        self.assertEqual(clock.sample(6), 3)
        self.assertEqual(clock.sample(99), 10)
        self.assertFalse(clock.running)
        clock.play(100)
        self.assertEqual(clock.position, 0)
        self.assertEqual(clock.sample(101), 2)
        clock.seek(-5, 101)
        self.assertEqual(clock.position, 0)
        for value in (float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                clock.seek(value)
            with self.assertRaises(ValueError):
                clock.set_speed(value)

    def test_independent_channel_clocks(self):
        ecg, bvp = Playback(60), Playback(60)
        ecg.play(0)
        bvp.play(5)
        self.assertEqual(ecg.sample(10), 10)
        self.assertEqual(bvp.sample(10), 5)
        bvp.pause(12)
        ecg.set_speed(2, 12)
        self.assertEqual(ecg.sample(15), 18)
        self.assertEqual(bvp.sample(15), 7)

    def test_packet_motion_does_not_precede_send(self):
        self.assertIsNone(packet_progress(10, 30, 9))
        self.assertEqual(packet_progress(10, 30, 10), 0)
        self.assertEqual(packet_progress(10, 30, 20), .5)
        self.assertEqual(packet_progress(10, 30, 60), 1)

    def test_continuous_seek_retains_causal_discrete_observations(self):
        c = DesktopController()
        c.seek_seconds(1799.9)
        self.assertEqual(max(c.model()['crew']['current_snapshot']['payload']['observations'], key=lambda o: o['observed_at'])['value'], 420)
        c.seek_seconds(1800)
        self.assertEqual(max(c.model()['crew']['current_snapshot']['payload']['observations'], key=lambda o: o['observed_at'])['value'], 585)
        self.assertEqual(max(c.model()['doctor']['current_snapshot']['payload']['observations'], key=lambda o: o['observed_at'])['value'], 420)
        c.seek_seconds(2399.9)
        self.assertIsNone(c.model()['crew']['protocol'])
        c.seek_seconds(2400)
        self.assertIsNotNone(c.model()['crew']['protocol'])
        c.seek_seconds(2520)
        self.assertEqual(len(c.model()['crew']['completions']), 2)
        c.seek_seconds(3899.9)
        self.assertFalse(c.model()['doctor']['followups'])
        c.seek_seconds(3900)
        self.assertEqual(len(c.model()['doctor']['followups']), 1)
        c.seek_seconds(1199.9)
        self.assertIsNone(c.model()['doctor']['current_snapshot'])
        self.assertFalse(c.model()['crew']['completions'])
        c.reset(interactive=True)
        with self.assertRaises(ValueError):
            c.seek_seconds(1)

    def test_recording_windows_use_real_source_times_and_bounded_points(self):
        for example in load_examples():
            record = RecordTrace(example)
            record.playback.seek(25)
            window = record.window()
            self.assertEqual(window['cursor'], example['times'][0]+25)
            self.assertLessEqual(len(window['points']), 900)
            for point in window['points']:
                idx = example['times'].index(point['source_seconds'])
                self.assertEqual(point['value'], example['values'][idx])
            self.assertEqual(window['synchronization'], 'unverified')


class TimerLifecycleTests(unittest.TestCase):
    def setUp(self):
        from biotwin.desktop import DesktopApp
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args, errors=self.errors: errors.append(args)
        self.app = DesktopApp(self.root, startup=False)
        self.root.update()

    def tearDown(self):
        if not self.app.closed:
            self.app.close()
        self.assertFalse(self.errors)

    def test_default_monitor_and_repeated_toggle_have_single_timer(self):
        self.assertEqual(self.app.screen, 'home')
        self.assertFalse(self.app.signal_playing)
        self.app.open_monitor('Crew 001')
        self.assertFalse(self.app.signal_playing)
        self.assertIsNone(self.app.startup_job)
        # Playback starts only after an explicit source Play action.
        self.app.select_region('heart'); self.app.toggle_signal('ECG')
        self.assertTrue(self.app.signal_playing)
        for _ in range(12):
            self.app.toggle_signal('ECG')
        self.assertTrue(self.app.signal_job)
        jobs = self.root.tk.call('after', 'info')
        self.assertEqual(len(jobs), 2)  # monitor + bounded local-load poll
        self.app.stop_signal()
        self.assertIsNone(self.app.signal_job)
        self.assertFalse(any(p['record'].playback.running for p in self.app.signal_panels.values() if p['record']))

    def test_tab_crew_mode_close_cancel_animation(self):
        self.app.open_monitor('Crew 001')
        self.app.stop_signal()
        self.root.update()
        self.app.toggle_play()
        self.assertTrue(self.app.play_job)
        self.assertIsNone(self.app.signal_job)
        self.app.crew_var.set('Crew 002')
        self.app.select_crew()
        self.assertFalse(self.app.playing)
        self.assertIsNone(self.app.play_job)
        self.assertIsNone(self.app.signal_panels['BVP']['record'])
        with patch('biotwin.desktop.messagebox.askyesno', return_value=True):
            self.app.new_operator()
        self.assertEqual(self.app.controller.current.mode, 'operator')
        self.app.open_monitor('Crew 002')
        self.app.stop_signal()
        self.root.update()
        self.app.toggle_signal('ECG')
        self.assertTrue(self.app.signal_job)
        self.app.close()
        self.assertIsNone(self.app.signal_job)
        self.assertIsNone(self.app.play_job)

    def test_immediate_close_cancels_startup_idle(self):
        from biotwin.desktop import DesktopApp
        self.app.close()
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda *args, errors=self.errors: errors.append(args)
        app = DesktopApp(root, startup=False)
        startup, poll = app.startup_job, app.poll_job
        app.close()
        jobs = root.tk.call('after', 'info')
        self.assertNotIn(startup, jobs)
        self.assertNotIn(poll, jobs)
        self.assertIsNone(app.startup_job)
        root.update()

    def test_missing_assets_constructs_unavailable_plots(self):
        from biotwin.desktop import DesktopApp
        self.app.close()
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda *args, errors=self.errors: errors.append(args)
        failure=ValueError('Test missing asset')
        with patch('biotwin.desktop.load_examples', side_effect=failure):
            app = DesktopApp(root, startup=False)
        # Mock keeps its exception object; detach the caught constructor frame
        # so later background reference loading cannot collect that Tk root.
        failure.__traceback__=None
        root.update()
        self.assertFalse(app.signal_playing)
        self.assertIn('SOURCE UNAVAILABLE', app.source_status.cget('text'))
        self.assertTrue(all(p['record'] is None for p in app.signal_panels.values()))
        app.close()

    def test_scrub_pauses_only_selected_record(self):
        self.app.open_monitor('Crew 001')
        self.app.select_region('heart')
        self.app.toggle_signal('ECG'); self.app.toggle_signal('BVP')
        self.app.scrub_signal('ECG', '12')
        ecg, bvp = [self.app.signal_panels[x]['record'].playback for x in ('ECG', 'BVP')]
        self.assertFalse(ecg.running)
        self.assertEqual(ecg.position, 12)
        self.assertTrue(bvp.running)
        self.app.reset_signal('BVP')
        self.assertFalse(bvp.running)
        self.assertEqual(bvp.position, 0)
        self.assertIsNone(self.app.signal_job)


if __name__ == '__main__':
    unittest.main()
