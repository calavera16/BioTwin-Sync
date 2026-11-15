export const STORY_DURATION = 30;
export const phases = [
  {start:0,label:'See the evidence',time:'00-06',title:'Start with recorded evidence.',detail:'This scripted mission uses a separate, synthetic response-speed check. The NASA recordings are shown alongside it.',status:'Before transmission',steps:[]},
  {start:6,label:'Send to Earth',time:'06-14',title:'Evidence travels to Earth.',detail:'The crew sends a measurement for human review. Local support stays available during the simulated delay.',status:'Evidence in transit',steps:[]},
  {start:14,label:'Earth reviews',time:'14-23',title:'Earth sees the earlier check.',detail:'A clinician writes conditional instructions. A newer onboard measurement arrives while the reply travels back.',status:'Reply in transit',steps:[]},
  {start:23,label:'Recheck the reply',time:'23-30',title:'Check the reply against now.',detail:'The local rule engine re-evaluates the human-written instructions using the newer onboard evidence.',status:'Reply arrived',steps:[]},
];
export function boundedTime(value) { return Number.isFinite(value) ? Math.max(0, Math.min(STORY_DURATION, value)) : 0; }
export function storyAt(value) {
  const seconds = boundedTime(value);
  const phase = phases.findLastIndex(p => seconds >= p.start);
  // Transit is compressed independently of the explanatory hold in each beat.
  const outbound = Math.max(0, Math.min(1, (seconds - 6) / 8));
  const inbound = Math.max(0, Math.min(1, (seconds - 14) / 9));
  return { seconds, phase, outbound, inbound, received: seconds >= 14, replied: seconds >= 23, complete: seconds === STORY_DURATION };
}
export function advanceStory(value, elapsed, playing) { return boundedTime(value + (playing ? Math.max(0, elapsed) : 0)); }
export function sourceRequest(example, offset = 0) { return '/api/source?' + new URLSearchParams({ example, start: String(Math.max(0, Math.min(50, offset))) }); }

// A missing example never inherits the story's successful branch.
export function exampleCheckView(proof, id = 'below') {
  const examples = proof?.examples, protocol = examples?.protocol;
  const item = Array.isArray(examples?.cases) ? examples.cases.find(item => item?.id === id) : null;
  const dep = item?.evidence?.dependencies?.[0], evaluation = item?.evaluation;
  const rules = protocol?.conditions;
  if (!item || !dep || !evaluation || !Array.isArray(item.evidence.dependencies) ||
      !Number.isFinite(protocol?.freshness_seconds) || protocol.freshness_seconds <= 0 ||
      !Number.isFinite(item.measurement_age_seconds) || !Number.isFinite(dep.current?.value) ||
      !Number.isFinite(dep.reviewed?.value) || !dep.current?.source_id || !dep.current?.method ||
      !['A','B','UNKNOWN'].includes(evaluation.outcome) || !Array.isArray(evaluation.checklist) ||
      !Array.isArray(evaluation.reasons) || !evaluation.reasons.every(reason => typeof reason === 'string') ||
      !evaluation.checklist.every(step => typeof step === 'string') ||
      !Array.isArray(protocol.checklists?.[evaluation.outcome]) ||
      JSON.stringify(evaluation.checklist) !== JSON.stringify(protocol.checklists[evaluation.outcome]) ||
      !['A','B'].every(branch => Array.isArray(rules?.[branch]) && rules[branch].length && rules[branch].every(rule =>
        rule && typeof rule.metric === 'string' && ['lt','le','eq','ge','gt'].includes(rule.op) && Number.isFinite(rule.value)))) return null;
  const selected = ['A','B'].includes(evaluation.outcome) && evaluation.checklist.length > 0 &&
    item.evidence.dependencies.every(dep => dep?.current?.compatible === true && dep.current.usable === true &&
      Number.isFinite(dep.current.value) && !dep.current.reason && dep.reviewed?.usable === true &&
      dep.reviewed.compatible === true && Number.isFinite(dep.reviewed.value)) &&
    item.measurement_age_seconds >= 0 && item.measurement_age_seconds <= protocol.freshness_seconds;
  return {item,dep,protocol,rules,evaluation,selected,steps:selected ? evaluation.checklist : []};
}

export function exampleDialogKey(key, shift, index, count) {
  if (key === 'Escape') return 'close';
  if (key !== 'Tab' || !count) return null;
  if (shift && index <= 0) return count - 1;
  if (!shift && (index < 0 || index === count - 1)) return 0;
  return null;
}

// Presentation follows the returned evaluator evidence, never the scripted beat.
// Unknown/missing gate fields must not become successful checks.
export function missionProofView(proof, seconds) {
  const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const phase = proof?.phases?.[seconds >= 23 ? 3 : seconds >= 19 ? 2 : seconds >= 14 ? 1 : 0];
  if (!record(phase?.crew?.metric) || !record(phase?.doctor?.metric) || !record(phase?.crew?.checklist)) return null;
  const checklist = phase.crew.checklist;
  const steps = Array.isArray(checklist.steps) ? checklist.steps.filter(step => typeof step === 'string') : [];
  const arrived = storyAt(seconds).replied;
  const dependencies = Array.isArray(phase.crew.protocol_evidence?.dependencies) ? phase.crew.protocol_evidence.dependencies.map(dep => record(dep) ? dep : {}) : [];
  const gates = dependencies.flatMap((dep, index) => {
    const current = record(dep.current) ? dep.current : null;
    const missing = typeof dep.metric !== 'string' || !dep.metric.trim() || !current || !Number.isFinite(current.value) || current.reason === 'missing dependency';
    const reason = current?.reason;
    const usable = current?.usable === true && (reason === null || reason === undefined);
    const label = dep.metric === 'reaction_time' ? 'Response-speed check' : String(dep.metric || 'Required measurement').replaceAll('_', ' ');
    const checks = [
      {id:`${index}-match`,measurement:label,state:missing || typeof current?.compatible !== 'boolean' ? 'missing' : current.compatible ? 'pass' : 'held',label:missing || typeof current?.compatible !== 'boolean' ? 'Source / method missing' : current.compatible ? 'Source / method match' : 'Source / method mismatch'},
      {id:`${index}-usable`,measurement:label,state:missing || typeof current?.usable !== 'boolean' ? 'missing' : usable ? 'pass' : 'held',label:missing || typeof current?.usable !== 'boolean' ? 'Freshness / quality missing' : usable ? 'Freshness / quality usable' : reason === 'stale dependency' ? 'Reading too old' : reason === 'future dependency evidence' ? 'Reading time invalid' : reason === 'unusable dependency' ? 'Reading needs review' : 'Evidence held'},
    ];
    if (!record(dep.reviewed) || !Number.isFinite(dep.reviewed.value)) checks.push({id:`${index}-reviewed`,measurement:label,state:'missing',label:'Reviewed evidence missing'});
    return checks;
  });
  const evidenceReady = dependencies.length > 0 && gates.every(gate => gate.state === 'pass');
  const evaluated = arrived && ['A','B'].includes(checklist.branch) && steps.length > 0 && evidenceReady;
  const evidenceMissing = dependencies.length === 0 || gates.some(gate => gate.state === 'missing');
  const outcome = !arrived ? 'Waiting for the reply' : evaluated ? 'Current checklist selected' : checklist.branch === 'UNKNOWN' || gates.some(gate => gate.state === 'held') ? 'Instruction held' : evidenceMissing ? 'Instruction evidence missing' : 'Evaluated checklist missing';
  return {phase,steps,gates,dependencies,evaluated,arrived,outcome};
}

// Plot source seconds directly. Null/nonfinite values and missing sample intervals
// break the trace; neither interpolation nor replacement physiology is generated.
export function sourceTrace(points = [], width = 1000, height = 190) {
  const timed = points.filter(p => Number.isFinite(p.t));
  const valid = timed.filter(p => Number.isFinite(p.value));
  if (!valid.length) return { path: '', start: timed[0]?.t, end: timed.at(-1)?.t, min: null, max: null };
  const start = timed[0].t, end = timed.at(-1).t;
  const values = valid.map(p => p.value), min = Math.min(...values), max = Math.max(...values);
  const deltas = timed.slice(1).map((p, i) => p.t - timed[i].t).filter(d => d > 0).sort((a,b) => a-b);
  const cadence = deltas.length ? deltas[Math.floor(deltas.length / 2)] : Infinity;
  let open = false, previous = null;
  const commands = [];
  for (const p of points) {
    if (!Number.isFinite(p.t) || !Number.isFinite(p.value)) { open = false; previous = null; continue; }
    if (previous !== null && (p.t <= previous || p.t - previous > cadence * 3)) open = false;
    const x = 16 + (p.t - start) / (end - start || 1) * (width - 32);
    const y = height - 18 - (p.value - min) / (max - min || 1) * (height - 36);
    commands.push(`${open ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`);
    open = true; previous = p.t;
  }
  return { path: commands.join(' '), start, end, min, max };
}
