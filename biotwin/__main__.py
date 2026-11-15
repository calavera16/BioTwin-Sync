"""Offline audit and explicit synthetic backend demo."""
import argparse
import html
import json
from collections import defaultdict
from pathlib import Path
from .data import CONTEXT, audit_directory, load_winscat, timestamp
from .cohort import find_similar
from .simulation import replay, synthetic_fixture


def demo(output_dir, data_dir):
    fixture = synthetic_fixture()
    report = {**fixture, "timeline": [replay(fixture["events"], at) for at in fixture["cutoffs"]]}
    if Path(data_dir, "winscat_campaign3").is_dir() and Path(data_dir, "bedrest0543_campaign3").is_dir():
        report["separate_real_source_audit"] = audit_directory(data_dir)
        observations, _ = load_winscat(Path(data_dir, "winscat_campaign3"))
        from .diagnostics import select_cohort_example
        report["separate_real_cohort_example"] = select_cohort_example(observations)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "timeline.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rows = []
    for state in report["timeline"]:
        crew = state["crew"]["current_snapshot"]
        doctor = state["doctor"]["current_snapshot"]
        evaluation = state["crew"]["evaluation"]
        def value(snapshot):
            return max(snapshot["payload"]["observations"], key=lambda o: o["observed_at"])["value"] if snapshot else "unreceived"
        rows.append(f"<tr><td>{html.escape(state['at'])}</td><td>{value(crew)} ms</td><td>{value(doctor)}{' ms' if doctor else ''}</td><td>{evaluation['outcome'] if evaluation else 'reply not received'}</td></tr>")
    final = report["timeline"][-1]["crew"]["evaluation"]
    body = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>BioTwin Sync — synthetic backend demo</title>
<style>body{{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 20px;color:#172638}}table{{border-collapse:collapse;width:100%}}td,th{{padding:12px;border:1px solid #bdcad5;text-align:left}}pre{{white-space:pre-wrap;background:#eff3f6;padding:16px}}strong{{color:#894b00}}</style>
<h1>BioTwin Sync: delayed reply, current evidence</h1><p><strong>{html.escape(fixture['label'])}</strong></p>
<p>Two 20-minute illustrative transmissions. The doctor only sees received evidence. At 09:30 the changed crew measurement remains untransmitted.</p>
<table><tr><th>Simulated UTC time</th><th>Crew latest</th><th>Doctor latest received</th><th>Crew checklist</th></tr>{''.join(rows)}</table>
<h2>Original reply preserved</h2><p>{html.escape(final['original_reply'])}</p>
<h2>Selected authored measurement checklist: {final['outcome']}</h2><ul>{''.join('<li>'+html.escape(s)+'</li>' for s in final['checklist'])}</ul>
<h2>Part A evidence</h2><pre>{html.escape(json.dumps(report['part_a'], indent=2))}</pre>
<h2>Measured transport bytes</h2><pre>{html.escape(json.dumps(report['transport'], indent=2))}</pre>
<p>SHA-256 identifies the specified project JSON bytes; it is not encryption or sender authentication. JSON Patch is not compression. No treatment or task clearance is issued.</p>
<h2>Separate historical NASA audit</h2><p>Real-source records are independent of the synthetic crew and timeline. Units and source timezone remain unverified; BEDREST drug/protocol conditions are unmapped.</p>
<pre>{html.escape(json.dumps(report.get('separate_real_source_audit', {'status':'not locally available'}), indent=2))}</pre>
<p>Machine-readable events, snapshots, comparisons, real cohort example and complete digests: <a href="timeline.json">timeline.json</a>.</p></html>"""
    (output / "report.html").write_text(body, encoding="utf-8")
    return {"json": str(output / "timeline.json"), "html": str(output / "report.html"), "outcome": final["outcome"]}


def headless_demo(output_dir='outputs/headless', data_dir='data/raw/nlsp'):
    from .workflow import complete_fixture
    from .viewmodel import view_model
    from .session import make_session, save_session, import_session
    from .diagnostics import dataset_report, self_check
    fixture = complete_fixture()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    session = make_session('headless-demo', fixture['events'], label=fixture['label'])
    session_path = save_session(session, output / 'sessions')
    if import_session(session_path) != session:
        raise ValueError('session roundtrip failed')
    models = [view_model(fixture['events'], at) for at in fixture['cutoffs']]
    diagnostics = dataset_report(data_dir)
    report = {'label': fixture['label'], 'four_stop_story': fixture['cutoffs'][:4], 'transport': fixture['transport'],
        'timeline': models, 'diagnostics': diagnostics, 'self_check': self_check(),
        'gui_status': 'Explicit placeholder; JSON is the desktop integration interface.'}
    model_path, report_path = output / 'view-model.json', output / 'development-report.json'
    model_path.write_text(json.dumps(models, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    readable = output / 'development-report.html'
    readable.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>BioTwin headless development report</title>'
        '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
        '<h1>BioTwin: complete headless consultation workflow</h1><p>' + html.escape(fixture['label']) + '</p>'
        '<p>09:00 consultation, 09:20 reviewer reply, 09:30 local change, 09:40 reply receipt; explicit operator steps at 09:41-43, '
        'new synthetic repeat at 09:44, follow-up sent 09:45 and received 10:05. Doctor receives no future evidence. '
        'The HTML is a development export; the graphical application remains a placeholder.</p>'
        '<h2>Checks and data limitations</h2><pre>' + html.escape(json.dumps({'checks': report['self_check'], 'diagnostics': diagnostics}, ensure_ascii=False, indent=2))
        + '</pre><h2>Explicit completion and follow-up</h2><pre>'
        + html.escape(json.dumps(models[-1]['doctor']['followups'], ensure_ascii=False, indent=2))
        + '</pre><p><a href="view-model.json">View model</a> | <a href="development-report.json">Complete report</a></p></html>', encoding='utf-8')
    return {'session': session_path, 'view_model': str(model_path), 'report_json': str(report_path), 'report_html': str(readable),
        'self_check': report['self_check']['status'], 'followups_received': len(models[-1]['doctor']['followups'])}


def signal_command(args):
    try:
        if args.command == 'waveform-preview':
            from .waveforms import load_waveform, replay_window
            return replay_window(load_waveform(args.data_dir, args.source_key), args.start_seconds, args.duration_seconds, args.max_points)
        from .quality import prepare_experiment, run_experiment, quality_status
        if args.command == 'quality-prepare':
            return prepare_experiment(args.data_dir, args.output_dir)
        if args.command == 'quality-benchmark':
            return run_experiment(args.prepared_dir, args.output_dir, args.review_receipt)
        return quality_status(args.output_dir)
    except (ImportError, RuntimeError) as exc:
        return {'status': 'unavailable', 'reason': str(exc), 'action': 'Install the pinned optional quality dependencies in an isolated environment; core workflow needs only Python.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('audit', 'demo', 'headless-demo', 'dataset-report'):
        item = sub.add_parser(command)
        item.add_argument('--data-dir', default='data/raw/nlsp')
        if command in ('demo', 'headless-demo'):
            item.add_argument('--output-dir', default='outputs/' + command)
        if command == 'dataset-report':
            item.add_argument('--quality-dir', default='outputs/quality/results')
    sub.add_parser('self-check')
    prepare = sub.add_parser('quality-prepare')
    prepare.add_argument('--data-dir', default='data')
    prepare.add_argument('--output-dir', default='outputs/quality')
    bench = sub.add_parser('quality-benchmark')
    bench.add_argument('--prepared-dir', default='outputs/quality')
    bench.add_argument('--output-dir', default='outputs/quality/results')
    bench.add_argument('--review-receipt', required=True)
    status = sub.add_parser('quality-status')
    status.add_argument('--output-dir', default='outputs/quality/results')
    wave = sub.add_parser('waveform-preview')
    wave.add_argument('source_key')
    wave.add_argument('--data-dir', default='data')
    wave.add_argument('--start-seconds', type=float, default=0)
    wave.add_argument('--duration-seconds', type=float, default=10)
    wave.add_argument('--max-points', type=int, default=1000)
    args = parser.parse_args()
    try:
        if args.command == 'audit':
            result = audit_directory(args.data_dir)
        elif args.command == 'demo':
            result = demo(args.output_dir, args.data_dir)
        elif args.command == 'headless-demo':
            result = headless_demo(args.output_dir, args.data_dir)
        elif args.command == 'dataset-report':
            from .diagnostics import dataset_report
            result = dataset_report(args.data_dir, args.quality_dir)
        elif args.command == 'self-check':
            from .diagnostics import self_check
            result = self_check()
        else:
            result = signal_command(args)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'BioTwin: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    if result.get('status') in ('failed', 'software-check-failed'):
        parser.exit(1)


if __name__ == '__main__':
    main()
