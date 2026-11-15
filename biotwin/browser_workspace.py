"""Data-only adapters for the browser-owned recorded workspace.

Import names are labels, never filesystem paths. Existing native validators run
against a private fixed-name temporary file; the browser cannot select a path.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile

from . import local_monitoring as local
from .session import MAX_BYTES as SESSION_MAX_BYTES

IMPORT_ACTIONS = {'import_observations', 'import_session', 'import_signal'}
LOCAL_ACTIONS = {'start_local', 'capture_manual', 'capture_reaction', *IMPORT_ACTIONS,
                 'mark_segment', 'request_repeat', 'resolve_repeat',
                 'assistant_preview', 'assistant_assess', 'assistant_complete'}
# Transport and decoded native limits are separate. Highly escaped imports can
# reach this cap before the native file limit; report the bounded failure.
MAX_IMPORT_BODY = 16 * 1024 * 1024


def import_text(controller, action, filename, text):
    if (type(filename) is not str or not 1 <= len(filename) <= 160
            or any(c in filename for c in '/\\:\x00')
            or filename.casefold() == '.env' or filename.casefold().startswith('.env.')
            or filename in ('.', '..')):
        raise ValueError('Choose a plain non-secret import filename.')
    suffix = Path(filename).suffix.casefold()
    allowed = {'.csv', '.json'} if action == 'import_observations' else {'.json'}
    limit = SESSION_MAX_BYTES if action == 'import_session' else local.MAX_BYTES
    if suffix not in allowed or type(text) is not str or len(text) > limit:
        raise ValueError('Unsupported import type or decoded byte limit exceeded.')
    raw = text.encode('utf-8')
    if len(raw) > limit:
        raise ValueError('Decoded import byte limit exceeded.')
    with tempfile.TemporaryDirectory(prefix='biotwin-browser-import-') as directory:
        path = Path(directory) / ('input' + suffix)
        path.write_bytes(raw)
        operation = {'import_observations': controller.import_observations,
                     'import_session': controller.open, 'import_signal': controller.add_signal}[action]
        operation(path)


def local_state(controller, role):
    """Never serialize raw supplements or current crew records to the doctor."""
    snapshot = controller.model()[role]['current_snapshot']
    rows = copy.deepcopy(snapshot['payload']['observations']) if snapshot else []
    crew = role == 'crew'
    supplement = controller.current.local_data if crew else None
    signals = []
    if supplement:
        for signal in supplement['signals']:
            signals.append({**{k: copy.deepcopy(v) for k, v in signal.items()
                               if k not in ('samples', 'sample_times_seconds')},
                            'sample_count': len(signal['samples'])})
    handoff = controller.assistant_handoff(role)
    return {'subject_id': snapshot['payload']['subject_id'] if snapshot else None,
            'storage': local.storage_status(controller.document()) if crew else None,
            'snapshot_digest': snapshot['digest'] if snapshot else None,
            'observations': rows, 'signals': signals,
            'segments': copy.deepcopy(supplement['segments']) if supplement else [],
            'repeat_requests': copy.deepcopy(supplement['repeat_requests']) if supplement else [],
            'assistant': {'catalog': controller.assistant_catalog(),
                          'context': controller.assistant_context() if crew else None,
                          'records': copy.deepcopy(handoff['records']),
                          'handoff': handoff}}


def export_session(controller):
    document = controller.document()
    text = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'
    limit = local.MAX_BYTES if controller.current.mode == 'local' else SESSION_MAX_BYTES
    if len(text.encode('utf-8')) > limit:
        raise local.StorageCapacityError() if controller.current.mode == 'local' else ValueError('Session export exceeds its native byte limit.')
    suffix = '.biotwin-local.json' if controller.current.mode == 'local' else '.biotwin-session.json'
    return {'filename': controller.current.session_id + suffix, 'text': text}


def signal_window(controller, signal_id, start):
    controller._require_local()
    if type(signal_id) is not str or not 1 <= len(signal_id) <= 160:
        raise ValueError('Choose a supported local signal.')
    rows = [s for s in controller.current.local_data['signals'] if s['id'] == signal_id]
    if len(rows) != 1 or type(start) is not int or not 0 <= start < len(rows[0]['samples']):
        raise ValueError('Choose an existing signal and valid sample offset.')
    signal = rows[0]
    stop = min(start + 900, len(signal['samples']))
    times = signal['sample_times_seconds']
    return {'signal_id': signal_id, 'start': start, 'end': stop,
            'sample_count': len(signal['samples']), 'unit': signal['unit'],
            'time_basis': signal['time_basis'], 'source_id': signal['source_id'],
            'points': [{'sample': i, 't': times[i] if times is not None else i / signal['sample_rate_hz'],
                        'value': signal['samples'][i]} for i in range(start, stop)]}
