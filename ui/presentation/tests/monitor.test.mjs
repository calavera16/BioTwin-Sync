import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {unlink} from 'node:fs/promises';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {monitorReading,packetProgress,captureTarget,workflowCue,evidenceEvents,activeIntake} from '../src/monitor.mjs';
import {chartGeometry} from '../src/model.mjs';
const compiled=new URL('../.test-monitor.mjs',import.meta.url);
await build({entryPoints:[new URL('../src/monitor.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],outfile:compiled.pathname.replace(/^\/([A-Za-z]:)/,'$1'),bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic'});
const {Monitor,DelayTrack}=await import(compiled.href);await unlink(compiled);
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
const row=(value,minute,extra={})=>({id:`row-${minute}`,subject_id:'subject-one',metric:'heart_rate',value,unit:'bpm',phase:'rest',method:'manual device reading',source_id:'device-a',quality:'usable',observed_at:`2026-11-14T09:${String(minute).padStart(2,'0')}:00Z`,...extra});
const state=(rows=[],role='crew')=>({role,crew:'Crew 001',mode:'local',session_id:'test',revision:2,at:'2026-11-14T09:10:00Z',actions:{},messages:[],transport:{packets:[]},projection:{status:'Collecting baseline',unknown:true,metric:{name:'reaction_time',value:null,unit:'ms',quality:'missing',history:[]},sensors:{},checklist:{steps:[],stale_completions:[]}},local:{observations:rows,assistant:{catalog:{},handoff:{records:[]}}}});

test('raw trend follows the latest exact subject/source/unit/phase/method context',()=>{
  const s=state([row(71,0),row(72,1),row(85,2,{source_id:'device-b'}),row(99,3,{unit:'other'}),row(88,4,{phase:'task'}),row(77,5,{method:'other'}),row(96,6,{subject_id:'other'}),row(73,7)]);
  const reading=monitorReading(s,'heart_rate');assert.equal(reading.value,73);assert.deepEqual(reading.points.map(p=>p.value),[71,72,73]);
  assert.equal(monitorReading(s,'reaction_time').name,'Reaction time');
});
test('review and missing latest rows do not fall back; gaps stay broken',()=>{
  const s=state([row(71,0),row(null,1,{quality:'missing'}),row(73,2),row(79,3,{quality:'review'})]);
  const reading=monitorReading(s,'heart_rate');assert.equal(reading.value,79);assert.equal(reading.reason,'Needs review');assert.deepEqual(reading.points.map(p=>p.value),[71,null,73,null]);assert.equal(chartGeometry(reading.points).segments.length,2);
  s.local.observations.push(row(null,4,{quality:'missing'}));assert.equal(monitorReading(s,'heart_rate').value,null);
});
test('equal latest instants abstain regardless of timezone spelling or row order',()=>{
  const rows=[row(71,0),row(72,1),row(90,1,{observed_at:'2026-11-14T18:01:00+09:00',source_id:'other'})];
  for(const ordered of [rows,rows.slice().reverse()]){const r=monitorReading(state(ordered),'heart_rate');assert.equal(r.value,null);assert.deepEqual(r.points,[]);assert.match(r.reason,/Conflicting/);}
});
test('microsecond latest ordering preserves precise history order and original timestamp',()=>{
  const rows=[row(71,0,{observed_at:'2026-11-14T09:00:00.000100Z'}),row(72,0,{observed_at:'2026-11-14T09:00:00.000200Z'})];
  for(const ordered of [rows,rows.slice().reverse()]){const r=monitorReading(state(ordered),'heart_rate');assert.equal(r.value,72);assert.equal(r.observed_at,rows[1].observed_at);assert.deepEqual(r.points.map(p=>p.value),[71,72]);assert.equal(r.points[0].t,r.points[1].t);}
});
test('microsecond ties normalize timezone and fraction spelling, while distinct instants do not tie',()=>{
  const first=row(71,0,{observed_at:'2026-11-14T09:00:00.000100Z'}),equal=row(72,0,{observed_at:'2026-11-14T18:00:00.000100+09:00'}),newer=row(73,0,{observed_at:'2026-11-14T04:00:00.000200-05:00'});
  for(const rows of [[first,equal],[equal,first]]){const r=monitorReading(state(rows),'heart_rate');assert.equal(r.value,null);assert.match(r.reason,/Conflicting/);}
  for(const rows of [[first,newer],[newer,first]])assert.equal(monitorReading(state(rows),'heart_rate').value,73);
  const short=row(75,0,{observed_at:'2026-11-14T09:00:00.1Z'}),padded=row(76,0,{observed_at:'2026-11-14T18:00:00.100000+09:00'});assert.equal(monitorReading(state([short,padded]),'heart_rate').value,null);
});
test('precise newest review or missing value never falls back to earlier usable microseconds',()=>{
  const older=row(71,0,{observed_at:'2026-11-14T09:00:00.999100Z'}),newer=row(79,0,{observed_at:'2026-11-14T09:00:00.999200Z',quality:'review'});
  let r=monitorReading(state([newer,older]),'heart_rate');assert.equal(r.value,79);assert.equal(r.reason,'Needs review');assert.deepEqual(r.points.map(p=>p.value),[71,null]);
  r=monitorReading(state([older,{...newer,value:null,quality:'missing'}]),'heart_rate');assert.equal(r.value,null);assert.equal(r.reason,'No reading');
});
test('missing and invalid times cannot become monitor readings or an invented trace',()=>{
  const s=state([row(888,0,{observed_at:'invalid'})]);let reading=monitorReading(s,'heart_rate');assert.equal(reading.value,null);assert.deepEqual(reading.points,[]);
  s.projection.sensors.HR={value:72,quality:'usable',unit:'bpm'};reading=monitorReading(s,'heart_rate');assert.equal(reading.value,72);assert.deepEqual(reading.points,[]);
});
test('separate BP and sleep tasks retain their actual units without manufacturing a pair',()=>{
  const s=state([row(122,0,{metric:'systolic_bp',unit:'mmHg'}),row(7.5,1,{metric:'sleep_duration',unit:'h'}),row(4,2,{metric:'sleep_quality',unit:'score/10'})]);
  assert.equal(monitorReading(s,'systolic_bp').value,122);assert.equal(monitorReading(s,'diastolic_bp').value,null);assert.equal(monitorReading(s,'sleep_duration').unit,'h');assert.equal(monitorReading(s,'sleep_quality').unit,'score/10');
});
test('monitor renders only role supplied numeric evidence, with absent categories explicit',()=>{
  const crew=render(Monitor,{state:state([row(137,0)])}),doctor=render(Monitor,{state:state([row(62,0)],'doctor')});
  assert.match(crew,/137/);assert.doesNotMatch(doctor,/137/);assert.match(doctor,/62/);assert.match(doctor,/delivered evidence only/);assert.doesNotMatch(doctor,/Record measurement|Reaction check<\/button>/);
  for(const category of ['Cognition','Cardiovascular','Oxygen','Temperature','Sleep'])assert.match(crew,new RegExp(category));
  assert.match(crew,/No reading/);assert.match(crew,/Reaction check only/);assert.match(crew,/Select cardiovascular measurements/);assert.match(crew,/class="human-figure"/);
});
test('urgent and stale warnings stay open in the monitor, without truncating urgent help',()=>{
  const s=state();s.local.assistant.handoff.urgent_history=['reported'];s.local.assistant.handoff.records=[{id:'assessment1',kind:'assessment',stale:true,context:{origin_created_at:'2026-11-15T09:00:00Z'}}];s.local.assistant.catalog.urgent_help='Use the mission emergency procedure.';
  const markup=render(Monitor,{state:s});assert.match(markup,/Reported urgent history remains/);assert.match(markup,/Use the mission emergency procedure/);assert.match(markup,/Earlier action context is stale/);assert.match(markup,/Personal reference/);
});

test('selected acquisition routes every supported metric to its actual method',()=>{
  const s=state();assert.deepEqual(captureTarget(s,'reaction_time'),{action:'capture_reaction',payload:{}});
  for(const metric of ['temperature','sleep_duration','heart_rate','systolic_bp','sleep_quality'])assert.deepEqual(captureTarget(s,metric),{action:'capture_manual',payload:{metric}});
  assert.equal(captureTarget({...s,role:'doctor'},'temperature'),null);assert.equal(captureTarget({...s,mode:'guided'},'temperature').action,'start_local');assert.equal(captureTarget({...s,mode:'operator'},'temperature').action,'capture');
});
test('UNKNOWN with zero applicable steps takes precedence over a usable unrelated reaction',()=>{
  const s=state();s.projection.metric={value:420,unit:'ms',quality:'usable',history:[]};s.projection.checklist.branch='UNKNOWN';s.actions.send_consultation=true;
  const cue=workflowCue(s);assert.equal(cue.kind,'missing');assert.equal(cue.tab,'selfcheck');assert.match(cue.label,/missing checklist evidence/);
  s.role='doctor';assert.equal(workflowCue(s).tab,'consultation');
});
test('only latest active routine intake controls staleness; urgent history stays distinct',()=>{
  const s=state(),records=[{id:'old',kind:'assessment',stale:true,context:{origin_created_at:'2026-11-15T09:00:00Z'}},{id:'action',kind:'action',stale:true,context:{origin_created_at:'2026-11-15T09:15:00Z'}},{id:'new',kind:'assessment',stale:false,context:{origin_created_at:'2026-11-15T09:10:00Z'}}];
  s.local.assistant.handoff.records=records;assert.equal(activeIntake(s).id,'new');assert.notEqual(workflowCue(s).kind,'stale');
  s.local.assistant.handoff.records=records.slice().reverse();assert.equal(activeIntake(s).id,'new');records[2].stale=true;assert.equal(workflowCue(s).tab,'assistant');
  s.local.assistant.handoff.urgent_history=['earlier report'];assert.equal(workflowCue(s).kind,'urgent');assert.match(workflowCue(s).detail,/not a current physiological assessment/);
});
test('current steps move to follow-up, in-transit waiting and delivered review using real API event ids',()=>{
  const s=state();s.projection.checklist={branch:'B',steps:['Repeat measurement','Record context'],completed_steps:[0],current_completion_count:1,completion_count:1,completions:[],followup_context:{basis:'current'}};s.actions.followup=true;
  assert.equal(workflowCue(s).kind,'steps');s.projection.checklist.completed_steps=[0,1];s.projection.checklist.current_completion_count=2;assert.equal(workflowCue(s).action,'followup');
  s.projection.checklist.completions=[{completed_at:'2026-11-15T09:05:00Z',stale_context:false}];s.messages=[{id:'event-followup-4',sender:'crew',kind:'followup',sent_at:'2026-11-15T09:06:00Z'}];s.transport.packets=[{event_id:'event-followup-4',status:'in transit',sender:'crew'}];assert.equal(workflowCue(s).kind,'waiting');
  s.transport.packets[0].status='arrived';assert.equal(workflowCue(s).label,'Review sent follow-up');assert.equal(workflowCue(s).tab,'consultation');
  s.projection.checklist.followup_context={basis:'historical',message:'Only historical records remain.'};s.projection.checklist.current_completion_count=0;assert.equal(workflowCue(s).kind,'stale');assert.equal(workflowCue(s).tab,'selfcheck');
});
test('mixed historical and renewed current completions do not hide current follow-up',()=>{
  const s=state();s.actions.followup=true;s.projection.checklist={branch:'A',steps:['Record repeat'],completed_steps:[0],current_completion_count:1,stale_completions:[{step_index:0}],followup_context:{basis:'current',message:'Current and historical records.'}};
  assert.equal(workflowCue(s).action,'followup');
});
test('packet events use only sent or arrived timestamps already visible at this terminal clock',()=>{
  const s=state();s.transport.packets=[{at:'2026-11-14T09:00:00Z',arrives_at:'2026-11-14T09:20:00Z',sender:'crew',status:'in transit'},{at:'2026-11-14T09:30:00Z',arrives_at:'2026-11-14T09:50:00Z',sender:'doctor',status:'in transit'}];
  assert.equal(evidenceEvents(s).length,1);assert.match(evidenceEvents(s)[0].label,/Crew packet sent/);
  s.at='2026-11-14T09:20:00Z';s.transport.packets[0].status='arrived';const events=evidenceEvents(s);assert.equal(events.length,2);assert.equal(events[1].t,Date.parse(s.at));assert.equal(events[1].label,'Earth received');
});
test('nonreaction source history receives no frontend-invented baseline policy',()=>{
  const s=state([row(70,0),row(71,1),row(72,2),row(73,3),row(74,4),row(175,5)]),reading=monitorReading(s,'heart_rate');
  assert.equal(reading.reference,undefined);assert.deepEqual(reading.points.map(p=>p.value),[70,71,72,73,74,175]);
});
test('packet position and countdown come from exact timestamps and remain bounded',()=>{
  const packet={at:'2026-11-14T09:00:00Z',arrives_at:'2026-11-14T09:20:00Z'};
  assert.deepEqual(packetProgress(packet,'2026-11-14T09:05:00Z'),{progress:.25,seconds:900});assert.equal(packetProgress(packet,'2026-11-14T08:00:00Z').progress,0);assert.deepEqual(packetProgress(packet,'2026-11-14T10:00:00Z'),{progress:1,seconds:0});
  for(const bad of [{...packet,at:'bad'},{...packet,arrives_at:packet.at},{...packet,arrives_at:'2026-11-14T08:00:00Z'}])assert.deepEqual(packetProgress(bad,'2026-11-14T09:05:00Z'),{progress:null,seconds:null});
});
test('Earth return packet reverses the marker but never renders transport payload contents',()=>{
  const s=state([],'doctor');s.at='2026-11-14T09:05:00Z';s.snapshot_digest='a1b2c3d4e5f60000';s.transport.packets=[{event_id:'p1',sender:'doctor',recipient:'crew',status:'in transit',at:'2026-11-14T09:00:00Z',arrives_at:'2026-11-14T09:20:00Z',payload:{text:'hidden-payload-123'}}];
  const markup=render(DelayTrack,{state:s});assert.match(markup,/class="packet-marker" x1="750" x2="750"/);assert.match(markup,/15:00 until next arrival/);assert.match(markup,/Earth → crew/);assert.match(markup,/25% of transit time elapsed/);assert.match(markup,/2\.40 AU/);assert.match(markup,/SHA-256 a1b2c3d4/);assert.doesNotMatch(markup,/hidden-payload-123|style=/);
});
