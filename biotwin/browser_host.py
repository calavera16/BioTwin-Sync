"""Private stdio-owned lifetime for the bundled desktop HTTP host.

stdout carries exactly one bounded readiness record. Diagnostics deliberately
exclude exception text, URLs and tokens. No browser or native window is opened.
"""
from __future__ import annotations

import json
import sys
from .presentation import PresentationHost

MAX_COMMAND = 128


def serve(stdin=None, stdout=None, stderr=None, host_factory=PresentationHost):
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    host = None
    try:
        host = host_factory()
        host.start()
        record = json.dumps({'type': 'ready', 'version': 1, 'url': host.url}, separators=(',', ':'))
        if len(record) > 1024:
            raise ValueError('Readiness bound exceeded.')
        stdout.write(record + '\n')
        stdout.flush()
        # EOF is the ownership signal: an orphaned host never remains running.
        while True:
            command = stdin.readline(MAX_COMMAND + 1)
            if not command or command == 'stop\n':
                break
            if len(command) > MAX_COMMAND or command not in ('ping\n',):
                raise ValueError('Unsupported parent command.')
        return 0
    except (Exception, KeyboardInterrupt):
        if stderr is not None:
            stderr.write('BioTwin local host stopped or could not start.\n')
            stderr.flush()
        return 1
    finally:
        if host is not None:
            host.close()


def main():
    return serve()
