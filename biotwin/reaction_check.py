"""Laptop short reaction check, independent of WinSCAT and clinical readiness.

The UI calls ``show_stimulus`` only after it has drawn the cue. Scheduling a
future cue never starts its response clock. All durations use monotonic time.
"""
from __future__ import annotations

import math
import random
import statistics
import time

TASK = 'laptop-simple-reaction-v1'


class ReactionCheck:
    def __init__(self, *, clock=time.monotonic, rng=None, trials=5,
                 foreperiod=(1.0, 3.0), response_limit=2.0, total_limit=60.0):
        if (type(trials) is not int or not 3 <= trials <= 10
                or len(foreperiod) != 2
                or not all(type(v) in (int, float) and math.isfinite(v) for v in (*foreperiod, response_limit, total_limit))
                or not .5 <= foreperiod[0] <= foreperiod[1] <= 5
                or not .5 <= response_limit <= 5 or not 10 <= total_limit <= 120):
            raise ValueError('Unsupported bounded reaction-task timing configuration.')
        self.clock=clock; self.rng=rng or random.SystemRandom(); self.trials=trials
        self.foreperiod=tuple(foreperiod); self.response_limit=response_limit; self.total_limit=total_limit
        self.state='new'; self.results=[]; self.started=None; self.due=None; self.onset=None; self.last=None

    def _now(self):
        now=self.clock()
        if type(now) not in (int,float) or not math.isfinite(now) or (self.last is not None and now < self.last):
            raise ValueError('Reaction clock must be finite and monotonic.')
        self.last=now
        return now

    def start(self):
        if self.state != 'new': raise ValueError('Task already started.')
        now=self._now(); self.started=now; self._wait(now)

    def _wait(self, now):
        if len(self.results) >= self.trials:
            self.state='finished'; return
        delay=self.rng.uniform(*self.foreperiod)
        if not self.foreperiod[0] <= delay <= self.foreperiod[1]: raise ValueError('Invalid foreperiod generator.')
        self.due=now+delay; self.onset=None; self.state='waiting'

    def tick(self):
        if self.state in ('new','finished','cancelled'): return self.state
        now=self._now()
        if now-self.started >= self.total_limit:
            self.cancel('total time limit'); return self.state
        if self.state=='stimulus' and now-self.onset >= self.response_limit:
            self.results.append({'outcome':'omission','reaction_ms':None})
            self._wait(now)
        return self.state

    def ready(self):
        self.tick()
        return self.state=='waiting' and self.last >= self.due

    def show_stimulus(self):
        self.tick()
        if self.state!='waiting' or self.last < self.due: raise ValueError('Stimulus is not due.')
        self.onset=self._now(); self.state='stimulus'

    def respond(self):
        count=len(self.results)
        self.tick()
        if len(self.results)!=count: return
        if self.state not in ('waiting','stimulus'): return
        now=self._now()
        if self.state=='waiting': result={'outcome':'false_start','reaction_ms':None}
        else:
            ms=(now-self.onset)*1000
            result={'outcome':'false_start' if ms < 100 else 'response','reaction_ms':ms}
        self.results.append(result); self._wait(now)

    def cancel(self, reason='operator cancelled'):
        if self.state not in ('finished','cancelled'):
            self.state='cancelled'; self.cancel_reason=reason

    def summary(self):
        if self.state!='finished': raise ValueError('Only a finished task can be recorded.')
        values=[r['reaction_ms'] for r in self.results if r['outcome']=='response']
        usable=len(values)>=3 and all(r['outcome']=='response' for r in self.results)
        return {'task_id':TASK,'value':statistics.median(values) if values else None,
            'unit':'ms','quality':'usable' if usable else ('review' if values else 'missing'),
            'trials':list(map(dict,self.results)), 'valid_responses':len(values),
            'false_starts':sum(r['outcome']=='false_start' for r in self.results),
            'omissions':sum(r['outcome']=='omission' for r in self.results),
            'configuration':{'trials':self.trials,'foreperiod_seconds':list(self.foreperiod),
                'response_limit_seconds':self.response_limit,'minimum_response_ms':100,'input_mode':'keyboard-space'},
            'limitations':'Laptop/display/input timing is uncalibrated. Not WinSCAT, diagnosis or validated readiness.'}
