"""Offline full/delta packets. RFC6902 subset: replace whole payload fields.

No add/remove/nested paths or extra envelope fields. Hashes establish project
byte identity, not encryption or sender authentication. Packet replay dedupes IDs.
"""
import copy
import hashlib
from .protocol import PAYLOAD_FIELDS, canonical_bytes, verify_snapshot
from .data import timestamp

FIELDS = {"schema_version", "kind", "base_digest", "target_digest", "content"}


def _forward(base, target):
    old, old_basis = timestamp(base["created_at"])
    new, new_basis = timestamp(target["created_at"])
    if old_basis != new_basis or new < old or (new == old and base != target):
        raise ValueError("backward, conflicting same-instant or incompatible target snapshot timing")


def _packet(kind, base, target, content):
    return {"schema_version": 1, "kind": kind, "base_digest": base, "target_digest": target, "content": content}


def full_packet(snapshot, base_digest=None):
    verify_snapshot(snapshot)
    return _packet("full", base_digest, snapshot["digest"], copy.deepcopy(snapshot["payload"]))


def make_delta(base_snapshot, target_snapshot):
    base, target = verify_snapshot(base_snapshot), verify_snapshot(target_snapshot)
    if base["subject_id"] != target["subject_id"]:
        raise ValueError("cannot delta across subjects")
    _forward(base, target)
    if base_snapshot["digest"] == target_snapshot["digest"]:
        raise ValueError("no-op delta")
    operations = [{"op": "replace", "path": "/" + key, "value": copy.deepcopy(target[key])} for key in sorted(PAYLOAD_FIELDS) if base[key] != target[key]]
    delta = _packet("delta", base_snapshot["digest"], target_snapshot["digest"], operations)
    full = full_packet(target_snapshot, base_snapshot["digest"])
    return delta if len(canonical_bytes(delta)) < len(canonical_bytes(full)) else full


def packet_sizes(base_snapshot, target_snapshot):
    base, target = verify_snapshot(base_snapshot), verify_snapshot(target_snapshot)
    delta = _packet("delta", base_snapshot["digest"], target_snapshot["digest"], [
        {"op": "replace", "path": "/" + key, "value": copy.deepcopy(target[key])} for key in sorted(PAYLOAD_FIELDS) if base[key] != target[key]])
    full = full_packet(target_snapshot, base_snapshot["digest"])
    return {"delta_bytes": len(canonical_bytes(delta)), "full_bytes": len(canonical_bytes(full)),
            "selected_kind": make_delta(base_snapshot, target_snapshot)["kind"],
            "serialization": "UTF-8 compact sorted-key project JSON; envelope included; no compression"}


def apply_delta(base_snapshot, envelope):
    """Apply selected full/delta envelope to exact base and verify target."""
    base = verify_snapshot(base_snapshot)
    canonical_bytes(envelope)
    if type(envelope) is not dict or set(envelope) != FIELDS or type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
        raise ValueError("invalid transport envelope")
    if envelope["base_digest"] != base_snapshot["digest"]:
        raise ValueError("wrong base digest; full snapshot resynchronization required")
    if envelope["target_digest"] == base_snapshot["digest"]:
        raise ValueError("duplicate/no-op target")
    if envelope["kind"] == "full":
        snapshot = {"payload": copy.deepcopy(envelope["content"]), "digest": envelope["target_digest"]}
    elif envelope["kind"] == "delta":
        payload = copy.deepcopy(base)
        operations = envelope["content"]
        if type(operations) is not list or not operations:
            raise ValueError("empty delta")
        seen = set()
        for op in operations:
            if type(op) is not dict or set(op) != {"op", "path", "value"} or op["op"] != "replace" or type(op["path"]) is not str or op["path"] not in {"/" + k for k in PAYLOAD_FIELDS} or op["path"] in seen:
                raise ValueError("unsupported/duplicate JSON Patch operation")
            seen.add(op["path"])
            payload[op["path"][1:]] = copy.deepcopy(op["value"])
        snapshot = {"payload": payload, "digest": hashlib.sha256(canonical_bytes(payload)).hexdigest()}
    else:
        raise ValueError("unknown transport kind")
    verify_snapshot(snapshot)
    _forward(base, snapshot["payload"])
    if snapshot["digest"] != envelope["target_digest"] or snapshot["payload"]["subject_id"] != base["subject_id"]:
        raise ValueError("wrong target digest/subject")
    return snapshot


def apply_resynchronization(current_snapshot, envelope):
    """Explicit full recovery; never used implicitly by apply_delta."""
    canonical_bytes(envelope)
    if type(envelope) is not dict or set(envelope) != FIELDS or type(envelope['schema_version']) is not int or envelope['schema_version'] != 1 or envelope['kind'] != 'full' or envelope['base_digest'] is not None:
        raise ValueError('resynchronization requires a standalone full envelope')
    target = {'payload': copy.deepcopy(envelope['content']), 'digest': envelope['target_digest']}
    payload = verify_snapshot(target)
    if current_snapshot is not None:
        current = verify_snapshot(current_snapshot)
        if current['subject_id'] != payload['subject_id']:
            raise ValueError('resynchronization subject mismatch')
        _forward(current, payload)
        if current['created_at'] == payload['created_at'] and current_snapshot['digest'] != target['digest']:
            raise ValueError('conflicting same-time resynchronization')
    return target
