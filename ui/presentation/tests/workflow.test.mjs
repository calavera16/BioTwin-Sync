import test from 'node:test';
import assert from 'node:assert/strict';
import {noviceWorkflow,journeySteps,manualDefaults,chronologicalRows,localDateTime,validateObservationTime,sessionSaveStatus,recordingLabel,technologyStatus} from '../src/workflow.mjs';
import {protocolGates,buildSampleSignal} from '../src/model.mjs';
const row=(value,time='2026-11-15T09:00:00Z',extra={})=>({id:'row',metric:'heart_rate',value,unit:'bpm',subject_id:'sub',source_id:'LOCAL:one:wrist-monitor',method:'optical pulse',phase:'laptop-local',quality:'usable',observed_at:time,...extra});
const state=()=>({mode:'local',role:'crew',crew:'Crew 001',session_id:'one',revision:4,actions:{},messages:[],transport:{packets:[]},local:{observations:[]},projection:{metric:{value:null,quality:'missing',history:[]},checklist:{steps:[],completed_steps:[]},sensors:{}}});
test('four-step guided journey offers explicit practice and cannot claim operator completion',()=>{
  const s=state();s.mode='guided';s.projection.checklist.steps=['Example instruction'];s.projection.checklist.completion_count=0;
  const flow=noviceWorkflow(s);assert.equal(journeySteps.length,4);assert.equal(flow.cue.action,'start_interactive');assert.match(flow.mode,/Read-only/);assert.equal(flow.progress[3],false);
  s.role='doctor';assert.equal(noviceWorkflow(s).cue.tab,'consultation');assert.equal(noviceWorkflow(s).cue.action,undefined);
});
test('real empty, recorded, in-transit and delivered evidence give a coherent next action',()=>{
  const s=state();assert.equal(noviceWorkflow(s,'heart_rate').cue.action,'capture_manual');assert.deepEqual(noviceWorkflow(s).progress,[false,false,false,false]);
  s.local.observations=[row(74)];s.actions.send_consultation=true;assert.equal(noviceWorkflow(s).cue.action,'send_consultation');
  s.messages=[{id:'sent',sender:'crew',kind:'snapshot',sent_at:'2026-11-15T09:00:00Z'}];s.transport.packets=[{event_id:'sent',status:'in transit'}];s.actions.advance=true;
  assert.equal(noviceWorkflow(s).cue.action,'advance');s.transport.packets[0].status='arrived';assert.equal(noviceWorkflow(s).cue.role,'doctor');
  s.role='doctor';s.actions.author_reply=true;assert.equal(noviceWorkflow(s).cue.action,'author_reply');
  s.messages.push({id:'reply',sender:'doctor',kind:'protocol'});s.transport.packets.push({event_id:'reply',status:'in transit'});assert.equal(noviceWorkflow(s).cue.action,'advance');
  s.transport.packets[1].status='arrived';s.snapshot_digest='reviewed-current';s.projection.protocol_evidence={reviewed_digest:'reviewed-current'};assert.equal(noviceWorkflow(s).cue.role,'crew');
});
test('held dependency and urgent history take precedence over convenience delivery actions',()=>{
  const s=state();s.actions.advance=true;s.transport.packets=[{status:'in transit'}];s.projection.checklist.branch='UNKNOWN';assert.equal(noviceWorkflow(s).cue.tab,'selfcheck');
  s.local.assistant={handoff:{urgent_history:['reported']}};assert.equal(noviceWorkflow(s).cue.kind,'urgent');assert.equal(noviceWorkflow(s).cue.tab,'assistant');
});
test('current received instructions and completion evidence advance to explicit follow-up',()=>{
  const s=state();s.projection.protocol_evidence={protocol_id:'p'};s.projection.checklist={steps:['Repeat reading'],completed_steps:[],current_completion_count:0};
  assert.equal(noviceWorkflow(s).cue.tab,'selfcheck');assert.equal(noviceWorkflow(s).progress[3],false);
  s.projection.checklist.completed_steps=[0];s.projection.checklist.current_completion_count=1;s.actions.followup=true;assert.equal(noviceWorkflow(s).cue.action,'followup');assert.equal(noviceWorkflow(s).progress[3],true);
});
test('manual prefill comes only from the matching metric and abstains on conflicting latest data',()=>{
  const s=state();s.local.observations=[row(74),row(36.8,'2026-11-15T09:01:00Z',{metric:'temperature',unit:'C',source_id:'LOCAL:one:thermometer',method:'oral thermometer'})];
  assert.deepEqual(manualDefaults(s,'heart_rate'),{source:'wrist-monitor',method:'optical pulse'});assert.deepEqual(manualDefaults(s,'temperature'),{source:'thermometer',method:'oral thermometer'});assert.deepEqual(manualDefaults(s,'sleep_quality'),{source:'',method:''});
  s.local.observations.push(row(80));assert.deepEqual(manualDefaults(s,'heart_rate'),{source:'',method:''});s.mode='guided';assert.deepEqual(manualDefaults(s,'temperature'),{source:'',method:''});
});
test('local input timestamp round-trips and rejects future real acquisition time regardless of mission clock',()=>{
  const now=Date.parse('2026-11-15T09:00:00Z'),local=localDateTime(now);
  assert.equal(Date.parse(local),now);assert.equal(validateObservationTime(local,now),'');assert.match(validateObservationTime(localDateTime(now+1000),now),/simulated mission clock/);assert.match(validateObservationTime('invalid',now),/date and time/);assert.equal(validateObservationTime(localDateTime(now-1000),now),'');
});
test('saved-file baseline, new work and export request stay distinct and session/revision-bound',()=>{
  const s=state(),opened={session_id:'one',revision:4,kind:'opened'},requested={...opened,kind:'requested'};
  assert.equal(sessionSaveStatus(s,null).kind,'unknown');assert.equal(sessionSaveStatus(s,{...opened,kind:'new'}).kind,'new');assert.equal(sessionSaveStatus(s,opened).kind,'opened');assert.match(sessionSaveStatus(s,requested).label,/verify saved file/);
  s.revision=5;assert.equal(sessionSaveStatus(s,opened).kind,'changed');assert.match(sessionSaveStatus(s,opened).label,/saved file/);s.session_id='other';assert.equal(sessionSaveStatus(s,opened).kind,'unknown');
});
test('measurement order respects original microseconds and timezone spelling without mutating rows',()=>{
  const older=row(74,'2026-11-15T09:00:00.000100Z'),newer=row(80,'2026-11-15T18:00:00.000200+09:00'),input=[newer,older];
  assert.deepEqual(chronologicalRows(input),[older,newer]);assert.deepEqual(input,[newer,older]);
});
test('technology gates never infer freshness or integrity verification from presence alone',()=>{
  const dep={reviewed:{source_id:'a',method:'optical'},current:{source_id:'b',method:'optical',quality:'usable',observed_at:'2026-11-15T09:00:00Z',compatible:false,usable:false,reason:'incompatible dependency'}};
  const gates=protocolGates(dep);assert.equal(gates[0].ok,false);assert.equal(gates[1].ok,true);assert.equal(gates[2].ok,null);assert.equal(gates[3].ok,null);assert.doesNotMatch(gates.map(g=>g.label).join(' '),/verified|authenticated/);
  dep.current.reason='stale dependency';assert.equal(protocolGates(dep)[2].ok,false);
});
test('recording menu names source and type without exposing UUID as its only identity',()=>{
  const signal={id:'random-id',channel:'ECG',source_id:'LOCAL:one:chest-recorder',sample_count:900,provenance:'recorded'};assert.match(recordingLabel(signal),/ECG.*chest-recorder.*recorded source.*900/);assert.doesNotMatch(recordingLabel(signal),/random-id/);signal.provenance='synthetic';assert.match(recordingLabel(signal),/synthetic demo/);
});

test('a new unsent snapshot does not reuse an earlier consultation delivery',()=>{
  const s=state();s.actions.send_consultation=true;s.local.observations=[row(80)];s.projection.snapshot_at='2026-11-15T09:00:00.000200Z';s.messages=[{sender:'crew',kind:'snapshot',sent_at:'2026-11-15T09:00:00.000100Z'}];
  const flow=noviceWorkflow(s);assert.equal(flow.cue.action,'send_consultation');assert.equal(flow.progress[1],true);
});

test('generated clean and corrupted sample waveforms retain synthetic provenance and review quality',()=>{
  for(const variant of ['clean','corrupted']){const signal=buildSampleSignal('sub','2026-11-15T09:00:00Z',variant);assert.equal(signal.provenance,'synthetic');assert.equal(signal.quality,'review');}
});

test('visible technology status uses actual dependency values and never borrows missing scenario outcomes',()=>{
  const s=state();assert.match(technologyStatus(s).label,/No rule evidence/);s.transport.packets=[{status:'in transit'}];assert.equal(technologyStatus(s).kind,'waiting');
  s.projection.protocol_evidence={dependencies:[{metric:'heart_rate',reviewed:{value:74,unit:'bpm'},current:{value:80,unit:'bpm',usable:true}}]};s.projection.checklist.branch='B';s.projection.checklist.steps=['Record the received instruction'];assert.match(technologyStatus(s).label,/reviewed 74 bpm.*onboard 80 bpm.*outcome B/);assert.equal(technologyStatus(s).kind,'evaluated');
  s.role='doctor';assert.match(technologyStatus(s).label,/delivered 80 bpm/);s.projection.protocol_evidence.dependencies[0].current.usable=false;assert.equal(technologyStatus(s).kind,'held');assert.doesNotMatch(technologyStatus(s).label,/selected/);
});

test('earlier consultation milestone remains after a newer reading, received instructions and delivered follow-up',()=>{
  const s=state();s.local.observations=[row(74),row(80,'2026-11-15T09:10:00Z')];s.projection.snapshot_at='2026-11-15T09:10:00Z';s.projection.protocol_evidence={protocol_id:'received-rule',dependencies:[]};s.projection.checklist={branch:'A',steps:['Record repeated reading'],completed_steps:[0],current_completion_count:1,completions:[{completed_at:'2026-11-15T09:40:00Z',stale_context:false}],followup_context:{basis:'current'}};s.actions.followup=true;s.messages=[{id:'original',sender:'crew',kind:'snapshot',sent_at:'2026-11-15T09:00:00Z'},{id:'followup',sender:'crew',kind:'followup',sent_at:'2026-11-15T09:41:00Z'}];s.transport.packets=[{event_id:'followup',status:'arrived',sender:'crew'}];
  const flow=noviceWorkflow(s);assert.deepEqual(flow.progress,[true,true,true,true]);assert.equal(flow.cue.label,'Review sent follow-up');
  s.messages=[];assert.equal(noviceWorkflow(s).progress[1],true);s.projection.protocol_evidence=null;s.projection.checklist.steps=[];s.messages=[{sender:'crew',kind:'followup'}];assert.equal(noviceWorkflow(s).progress[1],true);
});

test('a second delivered consultation retains authoring instead of following a historical reply onboard',()=>{
  const s=state();s.role='doctor';s.actions.author_reply=true;s.snapshot_digest='fresh-delivered-snapshot';s.local.observations=[row(80,'2026-11-15T10:00:00Z')];s.projection.snapshot_at='2026-11-15T10:00:00Z';s.projection.protocol_evidence={protocol_id:'earlier-reply',reviewed_digest:'earlier-reviewed-snapshot'};s.messages=[{id:'first',sender:'crew',kind:'snapshot',sent_at:'2026-11-15T09:00:00Z'},{id:'earlier-reply',sender:'doctor',kind:'protocol',sent_at:'2026-11-15T09:20:00Z'},{id:'second',sender:'crew',kind:'snapshot',sent_at:'2026-11-15T10:00:00Z'}];
  assert.equal(noviceWorkflow(s).cue.action,'author_reply');assert.equal(noviceWorkflow(s).cue.role,undefined);
  s.projection.protocol_evidence.reviewed_digest='fresh-delivered-snapshot';assert.equal(noviceWorkflow(s).cue.role,'crew');
  delete s.projection.protocol_evidence.reviewed_digest;assert.equal(noviceWorkflow(s).cue.action,'author_reply');
});

test('normal doctor null outcome with usable matching dependencies waits without claiming a held rule',()=>{
  const s=state();s.role='doctor';s.projection.protocol_evidence={dependencies:[{metric:'heart_rate',reviewed:{value:74,unit:'bpm'},current:{value:80,unit:'bpm',usable:true}}]};s.projection.checklist.branch=null;
  let status=technologyStatus(s);assert.equal(status.kind,'waiting');assert.match(status.label,/reviewed 74 bpm.*delivered 80 bpm.*Awaiting onboard evaluation/);assert.doesNotMatch(status.label,/Rule held|not established|selected instruction/);
  s.projection.checklist.branch='A';status=technologyStatus(s);assert.equal(status.kind,'waiting');assert.doesNotMatch(status.label,/selected instruction/);
  s.projection.checklist.steps=['Record current instruction'];assert.equal(technologyStatus(s).kind,'evaluated');
  s.projection.checklist.branch='UNKNOWN';assert.equal(technologyStatus(s).kind,'held');s.projection.checklist.branch=null;s.projection.protocol_evidence.dependencies[0].current.usable=false;assert.equal(technologyStatus(s).kind,'held');
});
