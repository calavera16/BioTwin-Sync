"""Private host ownership checks: no browser, filesystem export or credentials."""
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.parse import urlsplit, parse_qs
from http.client import HTTPConnection

from biotwin.browser_host import serve

ROOT = Path(__file__).resolve().parents[1]


class StubHost:
    url = 'http://127.0.0.1:12345/#token=' + 'A' * 43 + '&role=crew'
    def __init__(self): self.started = self.closed = False
    def start(self): self.started = True
    def close(self): self.closed = True


class BrowserHostTests(unittest.TestCase):
    def test_one_bounded_ready_and_eof_cleanup(self):
        host = StubHost()
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(serve(io.StringIO('ping\n'), output, errors, lambda: host), 0)
        self.assertTrue(host.started and host.closed)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(output.getvalue())['url'], host.url)
        self.assertEqual(errors.getvalue(), '')

    def test_stop_and_invalid_parent_commands_cleanup(self):
        for command, expected in [('stop\n', 0), ('unknown\n', 1), ('x' * 129, 1)]:
            host = StubHost()
            output, errors = io.StringIO(), io.StringIO()
            self.assertEqual(serve(io.StringIO(command), output, errors, lambda: host), expected)
            self.assertTrue(host.closed)
            self.assertNotIn('token', errors.getvalue())
            self.assertNotIn('A' * 43, errors.getvalue())

    def test_start_failure_has_no_exception_details_or_ready(self):
        host = StubHost()
        def fail(): raise ValueError(host.url)
        host.start = fail
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(serve(io.StringIO(), output, errors, lambda: host), 1)
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn(host.url, errors.getvalue())
        self.assertTrue(host.closed)

    def test_real_subprocess_http_and_owned_shutdown(self):
        for command in [b'stop\n', None]:
            process = subprocess.Popen([sys.executable, str(ROOT / 'run_webhost.py')],
                cwd=ROOT.parent, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                line = process.stdout.readline(1026)
                self.assertLessEqual(len(line), 1025)
                ready = json.loads(line)
                parsed = urlsplit(ready['url'])
                token = parse_qs(parsed.fragment)['token'][0]
                connection = HTTPConnection(parsed.hostname, parsed.port, timeout=5)
                connection.request('GET', '/api/state?role=crew&crew=Crew%20001',
                    headers={'X-BioTwin-Token': token})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn('projection', json.loads(response.read()))
                connection.close()
                if command: process.stdin.write(command); process.stdin.flush()
                process.stdin.close()
                self.assertEqual(process.wait(timeout=8), 0)
                self.assertEqual(process.stdout.read(), b'')
                self.assertEqual(process.stderr.read(), b'')
                closed = HTTPConnection(parsed.hostname, parsed.port, timeout=.5)
                with self.assertRaises(OSError): closed.connect()
                closed.close()
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=5)
                process.stdout.close(); process.stderr.close()


if __name__ == '__main__': unittest.main()
