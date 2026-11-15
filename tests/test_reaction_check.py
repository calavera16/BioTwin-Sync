import unittest

from biotwin.reaction_check import ReactionCheck


class Clock:
    value=0.0
    def __call__(self): return self.value


class RNG:
    def uniform(self,a,b): return a


class ReactionCheckTests(unittest.TestCase):
    def task(self):
        clock=Clock(); task=ReactionCheck(clock=clock,rng=RNG()); task.start()
        return task,clock

    def finish(self,task,clock):
        while task.state!='finished':
            clock.value=task.due+.05
            self.assertTrue(task.ready()); task.show_stimulus()
            clock.value+=.25; task.respond()

    def test_actual_draw_onset_not_scheduled_due(self):
        task,clock=self.task(); clock.value=task.due+.3
        self.assertTrue(task.ready()); task.show_stimulus(); clock.value+=.25; task.respond()
        self.assertAlmostEqual(task.results[0]['reaction_ms'],250)
        self.finish(task,clock); result=task.summary()
        self.assertEqual(result['quality'],'usable'); self.assertAlmostEqual(result['value'],250)
        self.assertEqual(result['valid_responses'],5)

    def test_false_start_and_omission_are_not_valid_responses(self):
        task,clock=self.task(); clock.value=.2; task.respond()
        self.assertEqual(task.results[0]['outcome'],'false_start')
        clock.value=task.due; task.show_stimulus(); clock.value+=2.1; task.respond()
        self.assertEqual(len(task.results),2)
        self.assertEqual(task.results[1]['outcome'],'omission')
        self.finish(task,clock); result=task.summary()
        self.assertEqual(result['quality'],'review'); self.assertEqual(result['valid_responses'],3)
        self.assertEqual(result['false_starts'],1); self.assertEqual(result['omissions'],1)

    def test_fast_anticipation_and_total_limit_cancel(self):
        task,clock=self.task(); clock.value=task.due; task.show_stimulus(); clock.value+=.05; task.respond()
        self.assertEqual(task.results[0]['outcome'],'false_start')
        clock.value=61; task.tick(); self.assertEqual(task.state,'cancelled')
        with self.assertRaises(ValueError): task.summary()

    def test_cancellation_clock_and_config_validation(self):
        task,clock=self.task(); task.cancel(); task.respond(); self.assertEqual(task.results,[])
        with self.assertRaises(ValueError): task.summary()
        task,clock=self.task(); clock.value=-1
        with self.assertRaises(ValueError): task.tick()
        for kwargs in ({'trials':2},{'response_limit':float('nan')},{'total_limit':1000}):
            with self.assertRaises(ValueError): ReactionCheck(**kwargs)


if __name__=='__main__': unittest.main()
