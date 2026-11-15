"""Deterministic role-isolated transport and human completion replay."""
import copy
from .data import CONTEXT
from .protocol import _aware, canonical_bytes, evaluate_protocol, make_snapshot, verify_snapshot
from .statistics import detect_change
from .transport import apply_delta, apply_resynchronization, packet_sizes


def _replay(events, at):
    from .workflow import completion_record, validate_protocol
    cutoff = _aware(at)
    roles = {r: {"inbox": [], "current_snapshot": None, "known_snapshots": {}, "protocol": None,
        "protocol_history": [], "authored_protocols": [], "protocol_sent_at": None, "evaluation": None, "received_packet_ids": [], "completions": [],
        "followups": [], "resynchronization_needed": False} for r in ("crew", "doctor")}
    actions, ids = [], set()
    for event in copy.deepcopy(list(events)):
        canonical_bytes(event)
        if type(event) is not dict:
            raise ValueError("event must be an object")
        event_id = event.get("event_id")
        if type(event_id) is not str or not event_id or event_id in ids:
            raise ValueError("missing/duplicate event ID")
        ids.add(event_id)
        if event.get("kind") in {"local", "completion"}:
            field = "snapshot" if event["kind"] == "local" else "record"
            if set(event) != {"event_id", "kind", "actor", "at", field} or event["actor"] not in roles:
                raise ValueError("invalid local/completion event")
            actions.append((_aware(event["at"]), 0 if field == "snapshot" else 2, event_id, event["kind"], event))
        elif event.get("kind") == "packet":
            if set(event) != {"event_id", "kind", "packet_id", "sender", "recipient", "sent_at", "arrives_at", "packet"}:
                raise ValueError("invalid packet event")
            if event["sender"] not in roles or event["recipient"] not in roles or event["sender"] == event["recipient"] or type(event["packet_id"]) is not str or not event["packet_id"]:
                raise ValueError("invalid packet identities")
            sent, arrival = _aware(event["sent_at"]), _aware(event["arrives_at"])
            if arrival <= sent:
                raise ValueError("arrival must be strictly after send")
            actions.extend(((sent, 3, event_id, "send", event), (arrival, 1, event_id, "arrival", event)))
        else:
            raise ValueError("unknown event kind")
    sent_ids, rejected = set(), []
    protocol_identities = {}

    def possess(role, snapshot):
        verify_snapshot(snapshot)
        if role["known_snapshots"].get(snapshot["digest"]) != snapshot:
            raise ValueError("sender does not possess exact snapshot")

    def adopt(role, snapshot):
        payload = verify_snapshot(snapshot)
        current = role["current_snapshot"]
        if current:
            if current["payload"]["subject_id"] != payload["subject_id"]:
                raise ValueError("subject switch requires a separate session")
            before, after = _aware(current["payload"]["created_at"]), _aware(payload["created_at"])
            if after < before or (after == before and snapshot["digest"] != current["digest"]):
                raise ValueError("out-of-order snapshot")
        role["known_snapshots"][snapshot["digest"]] = snapshot
        role["current_snapshot"] = snapshot

    for instant, _, event_id, action, event in sorted(actions, key=lambda x: x[:3]):
        if instant > cutoff:
            continue
        if action == "local":
            payload = verify_snapshot(event["snapshot"])
            if _aware(payload["created_at"]) > instant or any(_aware(o["observed_at"]) > _aware(payload["created_at"]) for o in payload["observations"]):
                raise ValueError("future local evidence")
            try:
                adopt(roles[event["actor"]], event["snapshot"])
            except ValueError as exc:
                raise ValueError("backward local snapshot or subject conflict") from exc
        elif action == "completion":
            role, record = roles[event["actor"]], event["record"]
            if type(record) is not dict or record.get("completed_at") != event["at"]:
                raise ValueError("invalid completion time")
            expected = completion_record(role, record.get("step_index"), event["at"], record.get("note"))
            if record != expected:
                raise ValueError("completion context mismatch")
            role["completions"].append(record)
        elif action == "send":
            packet, sender = event["packet"], roles[event["sender"]]
            if type(packet) is not dict:
                raise ValueError("invalid packet")
            kind = packet.get("type")
            if kind == "snapshot":
                if set(packet) != {"type", "snapshot"}:
                    raise ValueError("invalid snapshot packet")
                possess(sender, packet["snapshot"])
            elif kind == "protocol":
                if set(packet) != {"type", "protocol", "reviewed_snapshot"}:
                    raise ValueError("invalid protocol packet")
                possess(sender, packet["reviewed_snapshot"])
                validate_protocol(packet["protocol"], packet["reviewed_snapshot"])
                identity = (packet['protocol']['id'], packet['protocol']['version'])
                if identity in protocol_identities and protocol_identities[identity] != packet['protocol']:
                    raise ValueError('protocol ID/version reused for different authored content')
                protocol_identities[identity] = copy.deepcopy(packet['protocol'])
                sender['authored_protocols'].append(copy.deepcopy(packet))
            elif kind in {"envelope", "resync"}:
                if set(packet) != {"type", "envelope"}:
                    raise ValueError("invalid envelope packet")
                env = packet["envelope"]
                if type(env) is not dict:
                    raise ValueError("invalid envelope")
                if kind == "envelope":
                    base = sender["known_snapshots"].get(env.get("base_digest"))
                    if base is None:
                        raise ValueError("sender does not possess exact base")
                    target = apply_delta(base, env)
                else:
                    target = apply_resynchronization(None, env)
                possess(sender, target)
            elif kind == "followup":
                if set(packet) != {"type", "snapshot", "completions", "context_snapshots"} or type(packet["completions"]) is not list or not packet["completions"]:
                    raise ValueError("invalid follow-up packet")
                possess(sender, packet["snapshot"])
                if packet["snapshot"] != sender["current_snapshot"]:
                    raise ValueError("follow-up must carry current evidence at send")
                if packet["completions"] != sender["completions"]:
                    raise ValueError("follow-up must carry actual completion log")
                required = {c[k] for c in packet["completions"] for k in ("reviewed_digest", "current_digest")}
                if type(packet["context_snapshots"]) is not list or len(packet["context_snapshots"]) != len(required):
                    raise ValueError("invalid completion context snapshots")
                for snapshot in packet["context_snapshots"]:
                    possess(sender, snapshot)
                if {s["digest"] for s in packet["context_snapshots"]} != required:
                    raise ValueError("missing completion context")
            else:
                raise ValueError("unsupported packet contents")
            sent_ids.add(event_id)
        else:
            if event_id not in sent_ids:
                raise ValueError("packet arrived without a send")
            role, packet = roles[event["recipient"]], event["packet"]
            if event["packet_id"] in role["received_packet_ids"]:
                rejected.append({"event_id": event_id, "reason": "duplicate packet ID", "recipient": event["recipient"]})
                continue
            role["received_packet_ids"].append(event["packet_id"])
            status = "received"
            try:
                kind = packet["type"]
                if kind in {"snapshot", "followup"}:
                    snapshot = packet["snapshot"]
                    # Preserve late evidence in history/inbox without rolling back current state.
                    role["known_snapshots"][snapshot["digest"]] = snapshot
                    if kind == "followup":
                        role["followups"].append({"sent_at": event["sent_at"], "arrived_at": event["arrives_at"], **copy.deepcopy(packet)})
                    adopt(role, snapshot)
                elif kind in {"envelope", "resync"}:
                    if kind == "resync":
                        if not role["resynchronization_needed"] and role["current_snapshot"] is not None:
                            raise ValueError("unsolicited resynchronization")
                        target = apply_resynchronization(role["current_snapshot"], packet["envelope"])
                    else:
                        if role["current_snapshot"] is None or packet["envelope"]["base_digest"] != role["current_snapshot"]["digest"]:
                            role["resynchronization_needed"] = True
                            raise ValueError("full-resynchronization-needed: missing/wrong current base")
                        target = apply_delta(role["current_snapshot"], packet["envelope"])
                    adopt(role, target)
                    role["resynchronization_needed"] = False
                else:
                    role["protocol_history"].append(copy.deepcopy(packet))
                    role["known_snapshots"][packet["reviewed_snapshot"]["digest"]] = packet["reviewed_snapshot"]
                    if role["protocol"] and (
                        _aware(event['sent_at']) < _aware(role['protocol_sent_at']) or
                        _aware(packet['reviewed_snapshot']['payload']['created_at']) < _aware(role['protocol']['reviewed_snapshot']['payload']['created_at']) or
                        _aware(packet["protocol"]["valid_from"]) < _aware(role["protocol"]["protocol"]["valid_from"])):
                        raise ValueError("out-of-order protocol")
                    role["protocol"] = packet
                    role['protocol_sent_at'] = event['sent_at']
            except ValueError as exc:
                status = str(exc) + "; current state preserved"
                rejected.append({"event_id": event_id, "reason": str(exc), "recipient": event["recipient"]})
            role["inbox"].append({"packet_id": event["packet_id"], "sent_at": event["sent_at"],
                "received_at": event["arrives_at"], "packet_bytes": len(canonical_bytes(packet)), "packet": copy.deepcopy(packet), "status": status})
    for role in roles.values():
        if role["protocol"] and role["current_snapshot"]:
            packet = role["protocol"]
            role["evaluation"] = evaluate_protocol(packet["protocol"], packet["reviewed_snapshot"], role["current_snapshot"], at)
    return {"at": at, **roles, "rejected": rejected}


def replay(events, at):
    """Replay from zero; malformed input is a validation failure, never partial state."""
    try:
        return _replay(events, at)
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError('invalid replay schema or value') from exc


def synthetic_fixture():
    """420->585 ms engineering example; not a NASA record or medical outcome."""
    def observation(identity, value, clock):
        return {"id": identity, "subject_id": "SYNTHETIC:crew-01", "source_id": "SYNTHETIC:reaction-demo",
            "metric": "reaction_time", "value": value, "unit": "ms", "observed_at": f"2026-11-14T{clock}+00:00",
            "phase": "demo", "method": "synthetic fixed reaction task", "quality": "usable", "provenance": "synthetic"}
    history = [observation(f"baseline-{i}", value, f"08:{i * 5:02}:00") for i, value in enumerate((400, 410, 420, 430, 440, 420))]
    initial = observation("initial", 420, "09:00:00")
    changed = observation("changed", 585, "09:30:00")
    old = make_snapshot(initial["subject_id"], history + [initial], initial["observed_at"])
    new = make_snapshot(initial["subject_id"], history + [initial, changed], changed["observed_at"])
    analysis = detect_change(history + [initial], changed, persistence=1)
    # Threshold chosen and authored before changed evidence; mean + 2 sample SD
    # comes from the reviewed earlier reference, not future observations.
    reviewed_analysis = detect_change(history, initial, persistence=1)
    threshold = reviewed_analysis["reference"]["reference_interval"][1]
    dep = {key: initial[key] for key in CONTEXT}
    protocol = {"id": "SYNTHETIC:measurement-checklist", "version": 1, "subject_id": initial["subject_id"],
        "reviewed_digest": old["digest"], "valid_from": "2026-11-14T09:20:00+00:00",
        "valid_until": "2026-11-14T10:20:00+00:00", "dependencies": [dep], "freshness_seconds": 3600,
        "conditions": {"A": [{"metric": "reaction_time", "op": "le", "value": threshold}],
                       "B": [{"metric": "reaction_time", "op": "gt", "value": threshold}]},
        "original_reply": "Confirm acquisition conditions and record a repeat reaction-time measurement with context for review.",
        "checklists": {"A": ["Confirm the same task, device and acquisition conditions.", "Record a repeat measurement and context for the reviewer."],
            "B": ["Confirm task, device and acquisition conditions before repeating.", "Repeat the supported measurement and record distractions or interruptions.", "Prepare the new measurement and context as a follow-up packet for review."],
            "UNKNOWN": ["Record why evidence is unavailable or incompatible.", "Acquire a supported measurement with its method and time for review."]}}
    events = [{"event_id": "01-local", "kind": "local", "actor": "crew", "at": initial["observed_at"], "snapshot": old},
        {"event_id": "02-consultation", "kind": "packet", "packet_id": "consultation-1", "sender": "crew", "recipient": "doctor", "sent_at": initial["observed_at"], "arrives_at": "2026-11-14T09:20:00+00:00", "packet": {"type": "snapshot", "snapshot": old}},
        {"event_id": "03-reply", "kind": "packet", "packet_id": "reply-1", "sender": "doctor", "recipient": "crew", "sent_at": "2026-11-14T09:20:00+00:00", "arrives_at": "2026-11-14T09:40:00+00:00", "packet": {"type": "protocol", "protocol": protocol, "reviewed_snapshot": old}},
        {"event_id": "04-change", "kind": "local", "actor": "crew", "at": changed["observed_at"], "snapshot": new}]
    return {"label": "EXPLICITLY SYNTHETIC demonstration: not NASA physiological validation or treatment/clearance",
            "events": events, "part_a": {"reviewed": reviewed_analysis, "changed": analysis},
            "transport": packet_sizes(old, new), "threshold_origin": "reviewed past-only mean + 2 sample SD; illustrative reviewer-authored cutoff",
            "cutoffs": [f"2026-11-14T{clock}:00+00:00" for clock in ("09:00", "09:20", "09:30", "09:40")]}
