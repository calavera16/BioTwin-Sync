"""Read-only synthetic workflow proof. Never receives a live workspace controller."""
import copy
from datetime import timedelta
from .desktop_controller import DesktopController
from .mission import project_role, protocol_evidence
from .protocol import _aware, evaluate_protocol, make_snapshot
from .viewmodel import role_view


def _example_checks(role):
    """One authored protocol; only the isolated current observation changes."""
    packet = copy.deepcopy(role['protocol'])
    protocol, reviewed, now = packet['protocol'], packet['reviewed_snapshot'], role['at']
    template = max(role['current_snapshot']['payload']['observations'], key=lambda row: _aware(row['observed_at']))
    cases = []
    for name, label, value, age in (
        ('below', 'Below illustrative threshold', 420, 600),
        ('above', 'Above illustrative threshold', 585, 600),
        ('stale', 'Same 585 ms, too old', 585, protocol['freshness_seconds'] + 1),
    ):
        observation = {**copy.deepcopy(template), 'id': 'example-current', 'value': value,
                       'observed_at': (_aware(now) - timedelta(seconds=age)).isoformat()}
        snapshot = make_snapshot(protocol['subject_id'], [observation], now)
        cases.append({'id': name, 'label': label, 'measurement_age_seconds': age,
                      'current_snapshot': snapshot,
                      'evaluation': evaluate_protocol(protocol, reviewed, snapshot, now),
                      'evidence': protocol_evidence(packet, snapshot, now)})
    return {'protocol': protocol, 'reviewed_snapshot': reviewed, 'evaluated_at': now, 'cases': cases}


def mission_proof():
    controller = DesktopController()
    phases = []
    for seconds in (0, 1200, 1800, 2400):
        controller.seek_seconds(seconds)
        session = controller.current
        def scoped(role):
            projection = project_role(role_view(session.workflow.events, session.at, role))
            return {key: projection[key] for key in ('at', 'snapshot_at', 'digest', 'metric', 'reviewed', 'protocol_evidence', 'checklist')}
        phases.append({'mission_seconds': seconds, 'crew': scoped('crew'), 'doctor': scoped('doctor')})
    return {'label': 'Computed locally / synthetic workflow', 'metric': 'Synthetic reaction time (ms)',
            'context': 'Isolated guided fixture; independent of NASA recordings and user sessions.', 'phases': phases,
            'examples': _example_checks(role_view(session.workflow.events, session.at, 'crew'))}
