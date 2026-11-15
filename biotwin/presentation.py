"""Bounded offline browser workspace, isolated from native window sessions.

Only this module owns its controller and loopback HTTP thread. Imports use
private temporary validation files; renderer-managed exports are data only.
"""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from .desktop_assets import EXAMPLE_FILES, load_examples, preview
from .desktop_controller import CREWS, DesktopController
from .mission import project_role
from .mission_demo import mission_proof
from .viewmodel import role_view
from .browser_workspace import (LOCAL_ACTIONS, IMPORT_ACTIONS, MAX_IMPORT_BODY,
    import_text, local_state, export_session, signal_window)
from .session import MAX_EVENTS as MAX_SESSION_EVENTS
from .local_monitoring import StorageCapacityError, require_capacity

PUBLIC_FILES = ('index.html', 'app.js', 'app.css', 'THIRD-PARTY-NOTICES.txt')
ASSET_FILES = (*PUBLIC_FILES, 'manifest.json')
MAX_BODY = 16_384
MAX_EVENTS = 128


def asset_directory():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'assets' / 'presentation'


def load_assets(directory=None):
    """Read only exact assets, verifying the internal manifest before serving."""
    root = Path(directory) if directory is not None else asset_directory()
    if any(p.is_symlink() or p.is_junction() for p in (root, *root.parents)):
        raise ValueError('Linked presentation directories are unsupported.')
    path = root / 'manifest.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16_384:
        raise ValueError('Missing or unsafe presentation manifest.')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if (type(manifest) is not dict or set(manifest) != {'format', 'version', 'files'}
                or manifest['format'] != 'biotwin-presentation-assets'
                or type(manifest['version']) is not int or manifest['version'] != 1
                or type(manifest['files']) is not list or len(manifest['files']) != len(PUBLIC_FILES)):
            raise ValueError('Unsupported presentation manifest.')
        result = {}
        for entry in manifest['files']:
            if (type(entry) is not dict or set(entry) != {'file', 'bytes', 'sha256'}
                    or entry['file'] not in PUBLIC_FILES or entry['file'] in result
                    or type(entry['bytes']) is not int or not 0 < entry['bytes'] <= 3_000_000
                    or type(entry['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
                raise ValueError('Invalid presentation asset entry.')
            path = root / entry['file']
            if path.is_symlink() or not path.is_file() or path.stat().st_size != entry['bytes']:
                raise ValueError('Missing or unsafe presentation asset.')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('Presentation asset integrity mismatch.')
            result[entry['file']] = raw
        return result
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Malformed presentation manifest.') from exc


class PresentationError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _shape(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise PresentationError('Unsupported request fields.')


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise PresentationError('Numeric input is outside its supported range.')
    return value


def _text(value, maximum):
    if type(value) is not str or not value.strip() or len(value) > maximum or '\x00' in value:
        raise PresentationError('Text input is empty or exceeds its supported bound.')
    return value


class PresentationController:
    """One host workspace; request crew selection never escapes its lock."""
    def __init__(self):
        self._controller = DesktopController()
        self.revision = 0
        self.lock = threading.RLock()

    @staticmethod
    def _selection(role, crew):
        if role not in ('crew', 'doctor') or crew not in CREWS:
            raise PresentationError('Select a supported role and crew.')

    def state(self, role, crew):
        self._selection(role, crew)
        with self.lock:
            controller = self._controller
            session = controller.sessions[crew]
            model = role_view(session.workflow.events, session.at, role)
            projection = project_role(model)
            roster = []
            for name in CREWS:
                other = controller.sessions[name]
                scoped = project_role(role_view(other.workflow.events, other.at, role))
                roster.append({'id': name, 'status': scoped['status'], **{
                    k: scoped['metric'][k] for k in ('value', 'unit', 'observed_at')}})
            packets, messages = [], []
            now = datetime.fromisoformat(session.at)
            for event in session.workflow.events:
                if event['kind'] != 'packet' or datetime.fromisoformat(event['sent_at']) > now:
                    continue
                arrived = datetime.fromisoformat(event['arrives_at']) <= now
                packets.append({'event_id': event['event_id'], 'sender': event['sender'],
                    'recipient': event['recipient'], 'at': event['sent_at'], 'arrives_at': event['arrives_at'],
                    'status': 'arrived' if arrived else 'in transit'})
                if event['sender'] != role and not (event['recipient'] == role and arrived):
                    continue
                packet = event['packet']
                kind = packet['type']
                if kind == 'protocol':
                    text = packet['protocol']['original_reply']
                elif kind == 'snapshot':
                    rows = packet['snapshot']['payload']['observations']
                    latest = max(rows, key=lambda r: datetime.fromisoformat(r['observed_at'])) if rows else None
                    value = 'unavailable' if latest is None or latest['value'] is None else f"{latest['value']:g}"
                    text = (f"{latest['metric']}: {value} {latest['unit']} ({latest['observed_at']})"
                            if latest else 'No observations')
                elif kind == 'followup':
                    text = '\n'.join(c['note'] for c in packet['completions'])
                else:
                    continue
                messages.append({'id': event['event_id'], 'sender': event['sender'], 'recipient': event['recipient'],
                    'sent_at': event['sent_at'], 'received_at': event['arrives_at'] if arrived else None,
                    'text': text, 'kind': kind})
            pending = [p for p in packets if p['status'] == 'in transit']
            seconds = min((datetime.fromisoformat(p['arrives_at']) - now).total_seconds() for p in pending) if pending else None
            # Prerequisites are evaluated on a private candidate with request-local selection.
            scoped_controller = copy.deepcopy(controller)
            scoped_controller.select(crew)
            enabled = scoped_controller.prerequisites()
            if role == 'crew':
                projection['checklist']['followup_context'] = scoped_controller.followup_context()
            actions = {name: bool(enabled[key][0] and (role == required if required else True))
                for name, key, required in [('capture', 'capture', 'crew'), ('send_consultation', 'send', 'crew'),
                ('author_reply', 'author', 'doctor'), ('complete', 'complete', 'crew'),
                ('followup', 'followup', 'crew'), ('advance', 'advance', None)]}
            return {'version': 2, 'revision': self.revision, 'role': role, 'crew': crew,
                'session_id': session.session_id, 'mode': session.mode, 'at': session.at,
                'clock': {'basis': session.clock_basis, 'message':
                    'Replay time inferred from actual event times; this older file did not save its clock. Scheduled arrivals were not used.'
                    if session.clock_basis == 'inferred' else 'Saved replay time; delivery advances only with explicit session actions.'},
                'snapshot_digest': model['current_snapshot']['digest'] if model['current_snapshot'] else None,
                'crews': roster, 'projection': projection, 'transport': {'packets': packets,
                    'seconds_until_arrival': seconds}, 'messages': messages, 'actions': actions,
                'local': local_state(scoped_controller, role) if session.mode == 'local' else None}

    def read_bound(self, role, crew, session_id, revision, *, signal_id=None, start=0):
        self._selection(role, crew)
        if role != 'crew':
            raise PresentationError('Recorded files and raw signal inspection are crew-only.', 403)
        with self.lock:
            if type(revision) is not int or revision != self.revision or session_id != self._controller.sessions[crew].session_id:
                raise PresentationError('Evidence changed. Refresh before exporting or inspecting.', 409)
            candidate = copy.deepcopy(self._controller)
            candidate.select(crew)
            return export_session(candidate) if signal_id is None else signal_window(candidate, signal_id, start)

    def action(self, envelope):
        base = {'role', 'crew', 'action', 'expected_revision', 'payload'}
        if type(envelope) is not dict or set(envelope) not in (base, base | {'expected_session_id', 'expected_digest'}):
            raise PresentationError('Unsupported request fields.')
        role, crew, action, payload = (envelope[k] for k in ('role', 'crew', 'action', 'payload'))
        self._selection(role, crew)
        if type(envelope['expected_revision']) is not int or envelope['expected_revision'] < 0:
            raise PresentationError('A valid evidence revision is required.')
        if type(action) is not str:
            raise PresentationError('Unsupported action.')
        with self.lock:
            if envelope['expected_revision'] != self.revision:
                raise PresentationError('Evidence changed. Refresh before submitting again.', 409)
            if action in ('capture', 'send_consultation', 'complete', 'followup', 'reset_guided', 'start_interactive') and role != 'crew' or action == 'author_reply' and role != 'doctor':
                raise PresentationError('This action is unavailable to this role.', 403)
            # Publish only after validation and projection succeed: failed commands cannot mutate.
            candidate = copy.deepcopy(self._controller)
            candidate.select(crew)
            local_command = action in LOCAL_ACTIONS or candidate.current.mode == 'local'
            if local_command:
                if set(envelope) != base | {'expected_session_id', 'expected_digest'}:
                    raise PresentationError('Recorded commands require a session and snapshot binding.')
                snapshot = candidate.model()[role]['current_snapshot']
                digest = snapshot['digest'] if snapshot else None
                if envelope['expected_session_id'] != candidate.current.session_id or envelope['expected_digest'] != digest:
                    raise PresentationError('Session or evidence changed. Reopen this form.', 409)
            if action in LOCAL_ACTIONS and role != 'crew':
                raise PresentationError('Recorded capture and assistant actions are crew-only.', 403)
            if action not in ('seek', 'reset_guided', 'start_interactive', 'start_local', 'import_session') and candidate.current.mode not in ('operator', 'local'):
                raise PresentationError('Start an interactive synthetic session first.')
            if action == 'start_local':
                _shape(payload, ())
                candidate.start_local()
            elif action in IMPORT_ACTIONS:
                _shape(payload, ('filename', 'text'))
                try:
                    import_text(candidate, action, payload['filename'], payload['text'])
                except StorageCapacityError:
                    raise
                except ValueError as exc:
                    raise PresentationError(str(exc), 400) from exc
            elif action == 'capture_manual':
                _shape(payload, ('row',))
                if type(payload['row']) is not dict:
                    raise PresentationError('Measurement must be an object.')
                candidate.capture_manual(payload['row'])
            elif action == 'capture_reaction':
                _shape(payload, ('summary',))
                if type(payload['summary']) is not dict or payload['summary'].get('task_id') != 'browser-simple-reaction-v1':
                    raise PresentationError('Browser reaction acquisition metadata is required.')
                candidate.capture_reaction(payload['summary'])
            elif action == 'mark_segment':
                _shape(payload, ('signal_id', 'start', 'end', 'note'))
                for key in ('start', 'end'):
                    if type(payload[key]) is not int:
                        raise PresentationError('Segment offsets must be integers.')
                candidate.mark_segment(_text(payload['signal_id'], 160), payload['start'], payload['end'], _text(payload['note'], 1000))
            elif action == 'request_repeat':
                _shape(payload, ('signal_id', 'reason'))
                candidate.request_repeat(_text(payload['signal_id'], 160), _text(payload['reason'], 1000))
            elif action == 'resolve_repeat':
                _shape(payload, ('request_id', 'note'))
                candidate.resolve_repeat(_text(payload['request_id'], 160), _text(payload['note'], 1000))
            elif action in ('assistant_preview', 'assistant_assess'):
                _shape(payload, ('protocol_id', 'answers', 'note', 'context'))
                candidate._assistant_binding(payload['context'])
                if action == 'assistant_preview':
                    result = candidate.preview_assistant(payload['protocol_id'], payload['answers'], payload['note'])
                    return {'state': self.state(role, crew), 'preview': result}
                candidate.assess_assistant(payload['protocol_id'], payload['answers'], payload['note'], expected_context=payload['context'])
            elif action == 'assistant_complete':
                _shape(payload, ('assessment_id', 'step_id', 'note', 'context'))
                candidate.complete_assistant(payload['assessment_id'], payload['step_id'], payload['note'], expected_context=payload['context'])
            elif action == 'seek':
                _shape(payload, ('seconds',))
                seconds = _number(payload['seconds'], 0, 2400)
                if candidate.current.mode != 'guided':
                    raise PresentationError('Scene selection is available in guided mode.')
                for name in CREWS:
                    if candidate.sessions[name].mode != 'guided':
                        continue
                    candidate.select(name)
                    candidate.seek_seconds(seconds)
                candidate.select(crew)
            elif action in ('reset_guided', 'start_interactive'):
                _shape(payload, ())
                for name in CREWS:
                    if name == crew or candidate.sessions[name].mode != 'local':
                        candidate.reset(name, interactive=action == 'start_interactive')
            elif action == 'capture':
                _shape(payload, ('value',))
                candidate.capture(_number(payload['value'], 0, 10000))
            elif action == 'author_reply':
                fields = {'operator', 'threshold', 'original_reply', 'checklist_a', 'checklist_b'}
                if type(payload) is not dict or set(payload) not in (fields, fields | {'metric'}):
                    raise PresentationError('Unsupported reply fields.')
                if payload['operator'] not in ('lt', 'le', 'gt', 'ge'):
                    raise PresentationError('Unsupported comparison operator.')
                candidate.author_reply(payload['operator'], _number(payload['threshold'], 0, 10000),
                    _text(payload['original_reply'], 2000), _text(payload['checklist_a'], 3005),
                    _text(payload['checklist_b'], 3005), metric=_text(payload.get('metric', 'reaction_time'), 80))
            elif action == 'complete':
                _shape(payload, ('step', 'note'))
                if type(payload['step']) is not int or not 0 <= payload['step'] < 6:
                    raise PresentationError('Unsupported checklist step.')
                candidate.complete(payload['step'], _text(payload['note'], 2000))
            elif action == 'followup':
                if type(payload) is not dict or set(payload) not in (set(), {'confirm_historical'}):
                    raise PresentationError('Unsupported follow-up fields.')
                confirmation = payload.get('confirm_historical', False)
                if type(confirmation) is not bool:
                    raise PresentationError('Historical follow-up confirmation must be explicit.')
                context = candidate.followup_context()
                if context['requires_confirmation'] and not confirmation:
                    raise PresentationError(context['message'], 409)
                candidate.followup(confirm_historical=confirmation)
            elif action in ('advance', 'send_consultation'):
                _shape(payload, ())
                getattr(candidate, action)()
            else:
                raise PresentationError('Unsupported action.')
            if any(len(s.workflow.events) > MAX_SESSION_EVENTS for s in candidate.sessions.values()):
                raise PresentationError('Session event limit reached. Export and start a fresh workspace.')
            if candidate.current.mode == 'local': require_capacity(candidate.document())
            old = self._controller
            self._controller = candidate
            self.revision += 1
            try:
                return self.state(role, crew)
            except Exception:
                self._controller = old
                self.revision -= 1
                raise


class _LoopbackServer(HTTPServer):
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        # Disconnects and malformed requests never print request data/tracebacks.
        pass

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


class PresentationHost:
    def __init__(self, *, directory=None, examples_directory=None):
        self.directory = directory
        self.examples_directory = examples_directory
        self.controller = PresentationController()
        self._lifecycle = threading.RLock()
        self.server = None
        self.thread = None
        self.token = None
        self.origin = None

    def start(self):
        with self._lifecycle:
            if self.server is not None:
                return self
            assets = load_assets(self.directory)
            examples = {e['file']: e for e in load_examples(self.examples_directory)}
            proof = mission_proof()
            host = self

            class Handler(BaseHTTPRequestHandler):
                server_version = 'BioTwin'
                sys_version = ''

                def log_message(self, *_args):
                    pass

                def send_error(self, code, message=None, explain=None):
                    self._reply(code, {'error': 'Unsupported HTTP request.'})

                def _reply(self, status, body, content_type='application/json; charset=utf-8'):
                    if type(body) is not bytes:
                        body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
                    self.send_response(status)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(body)
                    self.close_connection = True

                def _run(self, mutation=False):
                    try:
                        if self.headers.get_all('Host') != [host.origin.removeprefix('http://')]:
                            raise PresentationError('Unsupported Host.', 403)
                        if len(self.path) > 2048:
                            raise PresentationError('Request path exceeds its bound.', 414)
                        parsed = urlsplit(self.path)
                        if parsed.scheme or parsed.netloc or parsed.fragment or '%' in parsed.path or '\\' in parsed.path:
                            raise PresentationError('Unsupported request path.', 404)
                        path = parsed.path
                        if path.startswith('/api/'):
                            tokens = self.headers.get_all('X-BioTwin-Token')
                            if len(tokens or []) != 1 or not secrets.compare_digest(tokens[0], host.token):
                                raise PresentationError('Access token required.', 403)
                            if self.headers.get_all('Origin') not in (None, [host.origin]):
                                raise PresentationError('Unsupported Origin.', 403)
                        if mutation:
                            if path not in ('/api/action', '/api/import') or parsed.query:
                                raise PresentationError('Unsupported endpoint.', 404)
                            if self.headers.get_all('Origin') != [host.origin]:
                                raise PresentationError('Same-origin command required.', 403)
                            if self.headers.get('Content-Type', '').lower() != 'application/json':
                                raise PresentationError('JSON content type required.', 415)
                            lengths = self.headers.get_all('Content-Length')
                            if self.headers.get('Transfer-Encoding') or len(lengths or []) != 1 or not re.fullmatch('[0-9]{1,8}', lengths[0]):
                                raise PresentationError('A bounded content length is required.')
                            length = int(lengths[0])
                            bound = MAX_IMPORT_BODY if path == '/api/import' else MAX_BODY
                            if not 1 <= length <= bound:
                                raise PresentationError('Request body exceeds its bound.', 413)
                            raw = self.rfile.read(length)
                            def unique(pairs):
                                value = {}
                                for key, item in pairs:
                                    if key in value:
                                        raise PresentationError('Duplicate JSON fields.')
                                    value[key] = item
                                return value
                            def invalid(_value):
                                raise PresentationError('Nonfinite JSON numbers are unsupported.')
                            envelope = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=invalid)
                            if type(envelope) is not dict or (envelope.get('action') in IMPORT_ACTIONS) != (path == '/api/import'):
                                raise PresentationError('Use the bounded import endpoint for file imports.')
                            result = host.controller.action(envelope)
                        elif path == '/api/state':
                            query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=2)
                            if set(query) != {'role', 'crew'} or any(len(v) != 1 for v in query.values()):
                                raise PresentationError('Role and crew are required.')
                            result = host.controller.state(query['role'][0], query['crew'][0])
                        elif path in ('/api/export', '/api/local-signal'):
                            query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=7)
                            fields = {'role', 'crew', 'session_id', 'revision'}
                            if path == '/api/local-signal':
                                fields |= {'signal_id', 'start'}
                            if set(query) != fields or any(len(v) != 1 for v in query.values()):
                                raise PresentationError('Role, crew and exact evidence binding are required.')
                            if not re.fullmatch('[0-9]{1,10}', query['revision'][0]):
                                raise PresentationError('Invalid evidence revision.')
                            options = {}
                            if path == '/api/local-signal':
                                if not re.fullmatch('[0-9]{1,5}', query['start'][0]):
                                    raise PresentationError('Invalid sample offset.')
                                options = {'signal_id': query['signal_id'][0], 'start': int(query['start'][0])}
                            result = host.controller.read_bound(query['role'][0], query['crew'][0],
                                query['session_id'][0], int(query['revision'][0]), **options)
                        elif path == '/api/mission-proof':
                            if parsed.query:
                                raise PresentationError('This proof has no query fields.')
                            result = proof
                        elif path == '/api/source-replay':
                            query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=1)
                            if set(query) != {'example'} or len(query['example']) != 1 or query['example'][0] not in EXAMPLE_FILES:
                                raise PresentationError('Select one bundled source example.')
                            name = query['example'][0]
                            example = examples[name]
                            duration = min(60, example['times'][-1] - example['times'][0] + 1/example['nominal_rate_hz'])
                            window = preview(example, example['times'][0], duration=duration, max_points=7200)
                            finite_values = [v for v in example['values'] if v is not None]
                            result = {'example': name, **{k: example[k] for k in ('source_key', 'source_sha256', 'channel', 'unit', 'doi', 'attribution', 'license', 'nominal_rate_hz')},
                                'context_label': 'NASA SOTERIA recorded source; another participant, separate from synthetic workflow.',
                                'duration': example['times'][-1] - example['times'][0],
                                'source_start': example['times'][0], 'source_end': example['times'][-1],
                                'raw_sample_count': window['sample_count'], 'preview_method': window['preview_method'],
                                'quality_reasons': window['quality_reasons'], 'synchronization': window['synchronization'],
                                'amplitude_min': min(finite_values) if finite_values else None,
                                'amplitude_max': max(finite_values) if finite_values else None,
                                'gap_intervals': [[a,b] for a,b in zip(example['times'], example['times'][1:]) if b-a > 3/example['nominal_rate_hz']],
                                'points': [{'t': p['source_seconds'], 'value': p['value']} for p in window['points']],
                                'author_exclusions': window['author_exclusions']}
                        elif path == '/api/source':
                            query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=2)
                            if not {'example'} <= set(query) <= {'example', 'start'} or any(len(v) != 1 for v in query.values()):
                                raise PresentationError('Select a bundled NASA source.')
                            name = query['example'][0]
                            if name not in EXAMPLE_FILES:
                                raise PresentationError('Unsupported source example.')
                            example = examples[name]
                            offset = _number(float(query.get('start', ['0'])[0]), 0, example['times'][-1] - example['times'][0])
                            window = preview(example, example['times'][0] + offset, duration=10, max_points=900)
                            result = {'example': name, **{k: example[k] for k in ('source_key', 'channel', 'unit', 'doi')},
                                'context_label': 'NASA SOTERIA source recording from another participant; separate from synthetic mission crew. Source synchronization and amplitude units unverified.',
                                'duration': min(10, example['times'][-1] - example['times'][0] - offset),
                                'points': [{'t': p['source_seconds'], 'value': p['value']} for p in window['points']],
                                'author_exclusions': window['author_exclusions']}
                        else:
                            name = 'index.html' if path == '/' else path.removeprefix('/')
                            if parsed.query or name not in assets or path not in ('/', *('/' + n for n in PUBLIC_FILES)):
                                raise PresentationError('Unsupported endpoint.', 404)
                            content_type = {'index.html': 'text/html; charset=utf-8', 'app.js': 'application/javascript; charset=utf-8',
                                'app.css': 'text/css; charset=utf-8', 'THIRD-PARTY-NOTICES.txt': 'text/plain; charset=utf-8'}[name]
                            self._reply(200, assets[name], content_type)
                            return
                        self._reply(200, result)
                    except StorageCapacityError as exc:
                        self._reply(507, {'error': str(exc), 'code': 'storage_capacity', 'draft_retained': True})
                    except PresentationError as exc:
                        self._reply(exc.status, {'error': str(exc)[:240]})
                    except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
                        self._reply(400, {'error': 'Invalid request or unsupported workflow input.'})
                    except (TimeoutError, ConnectionError, OSError):
                        self.close_connection = True
                    except Exception:
                        self._reply(500, {'error': 'Presentation command failed.'})

                def do_GET(self):
                    self._run()

                def do_POST(self):
                    self._run(mutation=True)

            server = _LoopbackServer(('127.0.0.1', 0), Handler)
            self.token = secrets.token_urlsafe(32)
            self.origin = f'http://127.0.0.1:{server.server_address[1]}'
            self.server = server
            self.thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, name='BioTwin-presentation', daemon=True)
            self.thread.start()
            return self

    @property
    def url(self):
        if self.server is None:
            raise ValueError('Presentation host is closed.')
        return f'{self.origin}/#token={self.token}&role=crew'

    def close(self):
        with self._lifecycle:
            if self.server is None:
                return
            server, thread = self.server, self.thread
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            self.server = self.thread = self.token = self.origin = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *_args):
        self.close()


def smoke_check():
    """Exercise the actual packaged HTTP transport without opening a browser."""
    from http.client import HTTPConnection
    with PresentationHost() as host:
        def request(method, path, body=None):
            connection = HTTPConnection('127.0.0.1', host.server.server_address[1], timeout=5)
            headers = {'X-BioTwin-Token': host.token, 'Origin': host.origin, 'Content-Type': 'application/json'}
            connection.request(method, path, json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            raw = response.read()
            status = response.status
            connection.close()
            return status, raw
        status, raw = request('GET', '/')
        checks = {'static': status == 200 and b'<html' in raw.lower()}
        status, raw = request('POST', '/api/action', {'role': 'crew', 'crew': CREWS[0],
            'action': 'seek', 'expected_revision': 0, 'payload': {'seconds': 1800}})
        crew = json.loads(raw)
        status2, raw = request('GET', '/api/state?role=doctor&crew=Crew%20001')
        doctor = json.loads(raw)
        checks['role_isolation'] = (status == status2 == 200 and crew['projection']['metric']['value'] == 585
            and doctor['projection']['metric']['value'] == 420 and doctor['crews'][0]['value'] == 420)
        status, raw = request('GET', '/api/source?example=crew001_ecg.json&start=0')
        source = json.loads(raw)
        checks['source'] = status == 200 and bool(source['points']) and 'another participant' in source['context_label']
        thread = host.thread
    checks['shutdown'] = not thread.is_alive()
    return checks
