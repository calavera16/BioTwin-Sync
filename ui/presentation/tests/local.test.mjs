import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {unlink,readFile} from 'node:fs/promises';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {BrowserReaction} from '../src/reaction.mjs';
import {contextOf,sameContext,importByteLimit,assistantToolTarget,signalWindowVisible,PreviewGate,canSubmitContext} from '../src/model.mjs';
const compiled=new URL('../.test-local.mjs',import.meta.url);
await build({entryPoints:[new URL('../src/local.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],outfile:compiled.pathname.replace(/^\/([A-Za-z]:)/,'$1'),bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic'});
const {Measurements,Assistant,LocalEditor,ObservationTable,safePastTime}=await import(compiled.href);await unlink(compiled);
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
test('generated sample times never run ahead of the real clock or the advanced session clock',()=>{
  const ahead=new Date(Date.now()+40*60000).toISOString(),behind='2026-01-01T00:00:00Z';
  assert.ok(Date.parse(safePastTime(ahead,0))<=Date.now());
  assert.equal(safePastTime(behind,5),'2025-12-31T23:55:00Z');
  assert.ok(Date.parse(safePastTime('invalid',0))<=Date.now());
  assert.match(safePastTime(behind),/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/);
});
test('CSV import shows schema with a session-safe example; storage recovery only appears for capacity problems',()=>{
  const s=state('crew');s.at='2026-01-01T00:00:00Z';s.local.storage={bytes:10,limit_bytes:2097152};
  const draft={action:'import_observations',context:contextOf(s),payload:{}};
  const form=render(LocalEditor,{state:s,draft,onExport:()=>{}});
  assert.match(form,/subject_id,metric,value,unit,observed_at,source_id,method,quality/);assert.match(form,/2025-12-31T23:55:00Z/);
  const csvError=render(LocalEditor,{state:s,draft,error:'CSV is missing required column(s): subject_id.',onExport:()=>{}});
  assert.match(csvError,/missing required column/);assert.doesNotMatch(csvError,/Save session without closing draft/);
});

test('reaction onset is tied to the committed cue, not a delayed scheduling callback',()=>{
  let now=0;const task=new BrowserReaction(()=>now,()=>0);task.start();
  now=1500;assert.equal(task.tick(),'drawing');now=1540;task.showCue();now=1840;task.respond();
  assert.equal(task.trials[0].reaction_ms,300);assert.equal(task.phase,'waiting');
  for(const ms of [400,200,600,500]){now+=1000;task.tick();task.showCue();now+=ms;task.respond();}
  const summary=task.summary();assert.equal(summary.value,400);assert.equal(summary.quality,'usable');assert.equal(summary.trials.length,5);assert.equal(summary.task_id,'browser-simple-reaction-v1');assert.equal(summary.timing_clock,'performance.now');assert.equal(summary.focus_visibility_guard,true);assert.equal(summary.cancelled,false);
  summary.trials[0].reaction_ms=99;assert.equal(task.trials[0].reaction_ms,300);
});
test('early responses, omissions and sub-100ms inputs cannot become usable reaction data',()=>{
  let now=0;const task=new BrowserReaction(()=>now,()=>0);task.start();now=500;task.respond();
  now=1500;task.tick();task.showCue();now+=50;task.respond();
  now+=1000;task.tick();task.showCue();now+=2100;task.respond(); // timeout must not consume a second early trial
  assert.equal(task.trials.length,3);assert.equal(task.trials[2].outcome,'omission');
  for(let i=0;i<2;i++){now+=1000;task.tick();task.showCue();now+=250;task.respond();}
  const summary=task.summary();assert.equal(summary.quality,'review');assert.equal(summary.false_starts,2);assert.equal(summary.omissions,1);assert.equal(summary.valid_responses,2);assert.equal(summary.value,250);
});
test('cancelled, unfinished and backwards-clock reaction checks are unsaveable',()=>{
  let now=0;const task=new BrowserReaction(()=>now,()=>0);task.start();assert.throws(()=>task.summary());task.cancel('Focus lost');now=5000;assert.equal(task.tick(),'cancelled');assert.throws(()=>task.summary());
  const back=new BrowserReaction(()=>now,()=>0);back.start();now=4999;assert.throws(()=>back.tick(),/clock changed/);
});
test('foreperiod range, total deadline and all-omission unknown state remain bounded',()=>{
  let now=0;const task=new BrowserReaction(()=>now,()=>.999999);task.start();assert.ok(task.due>=1000&&task.due<3000);now=60000;assert.equal(task.tick(),'cancelled');assert.throws(()=>task.summary());
  now=0;const missing=new BrowserReaction(()=>now,()=>0);missing.start();for(let i=0;i<5;i++){now+=1000;missing.tick();missing.showCue();now+=2000;missing.tick();}assert.equal(missing.summary().value,null);assert.equal(missing.summary().quality,'missing');
});
const catalog={disclaimer:'Educational prototype',protocols:[{id:'headache',title:'Headache',questions:[{id:'urgent',text:'Urgent signs?'}]}],sources:[]};
const state=role=>({role,crew:'Crew 001',mode:'local',session_id:'recorded-1',revision:2,snapshot_digest:role==='doctor'?null:'recorded-digest',projection:{digest:'display-digest'},local:{subject_id:'local-subject',observations:[],assistant:{catalog,context:role==='crew'?{evidence_digest:'e1'}:null,handoff:{records:[],text:'No delivered records'},records:[]}}});
test('recorded actions bind to the actual snapshot digest, including no delivered evidence',()=>{
  assert.equal(contextOf(state('crew')).digest,'recorded-digest');assert.equal(contextOf(state('doctor')).digest,null);
  const draft=contextOf(state('crew'));assert.equal(sameContext(draft,contextOf({...state('crew'),snapshot_digest:'changed'})),false);
});
test('crew can capture actual inputs while delivered doctor view cannot reveal acquisition tools',()=>{
  const crew=render(Measurements,{state:state('crew')}),doctor=render(Measurements,{state:state('doctor')});
  assert.match(crew,/Run reaction check/);assert.match(crew,/Record a measurement/);assert.doesNotMatch(doctor,/Run reaction check|Import CSV|Record a measurement/);assert.match(doctor,/Delivered observations only/);
  const intake=render(Assistant,{state:state('crew')}),handoff=render(Assistant,{state:state('doctor')});assert.match(intake,/New intake/);assert.doesNotMatch(handoff,/New intake/);assert.match(handoff,/Received evidence only/);
});
test('intake defaults every safety answer to unsure and keeps opening assistant evidence',()=>{
  const s=state('crew'),draft={action:'assistant_assess',context:contextOf(s),payload:{protocol_id:'headache',answers:{},note:'',context:{evidence_digest:'e1'}}};
  const markup=render(LocalEditor,{state:s,draft});assert.match(markup,/value="unsure" selected/);assert.match(markup,/Preview guidance/);assert.match(markup,/Save intake/);
  const stale=render(LocalEditor,{state:{...s,revision:3},draft});assert.match(stale,/Evidence changed while this form was open/);assert.match(stale,/disabled/);
});
test('manual editor starts with selected temperature or sleep method and retains capacity recovery',()=>{
  const s=state('crew');s.local.storage={bytes:1800000,limit_bytes:2097152};
  for(const [metric,unit] of [['temperature','C'],['sleep_duration','h'],['sleep_quality','score/10']]){
    const draft={action:'capture_manual',context:contextOf(s),payload:{metric}};
    const markup=render(LocalEditor,{state:s,draft,error:'Storage capacity reached. Your draft is retained.',onExport:()=>assert.fail('Rendering cannot export')});
    assert.match(markup,new RegExp('value="'+metric+'" selected'));assert.ok(markup.includes('Value ('+unit+')'));assert.match(markup,/Your draft is retained/);assert.match(markup,/Save session without closing draft/);assert.doesNotMatch(markup,/Evidence changed while this form was open/);
  }
});
test('acquisition guards cancel focus/visibility loss and never read secret files',async()=>{
  const source=await readFile(new URL('../src/local.jsx',import.meta.url),'utf8');assert.match(source,/addEventListener\('blur',blur\)/);assert.match(source,/addEventListener\('visibilitychange',hidden\)/);assert.match(source,/task.current\?\.cancel\('Closed'\)/);
  assert.ok(source.indexOf("file.name.toLowerCase().startsWith('.env.')")<source.indexOf('await file.text()'));
  const app=await readFile(new URL('../src/app.jsx',import.meta.url),'utf8');assert.match(app,/expected_session_id:context.session_id,expected_digest:context.digest/);assert.match(app,/new Blob\(\[result.text\]/);assert.doesNotMatch(app,/section-label/);
});
test('signal windows cannot survive a blank selection, removed signal or changed session evidence',()=>{
  const context=contextOf(state('crew')),data={signal_id:'signal-1',context};
  assert.equal(signalWindowVisible(data,'signal-1',context),true);
  for(const selected of ['',undefined,'signal-2'])assert.equal(signalWindowVisible(data,selected,context),false);
  assert.equal(signalWindowVisible(data,'signal-1',{...context,revision:3}),false);
  assert.equal(signalWindowVisible(data,'signal-1',{...context,session_id:'imported'}),false);
});
test('late preview responses are invalidated by answer or protocol edits',()=>{
  const gate=new PreviewGate(),request=gate.open();assert.equal(gate.accept(request),true);gate.invalidate();assert.equal(gate.accept(request),false);
  const next=gate.open();gate.invalidate();assert.equal(gate.accept(next),false);assert.equal(gate.accept(gate.open()),true);
});
test('delayed file reads cannot submit an old role/crew context after navigation',()=>{
  const s=state('crew'),opening={...contextOf(s),epoch:4};assert.equal(canSubmitContext(opening,s,4),true);
  assert.equal(canSubmitContext(opening,null,5),false);assert.equal(canSubmitContext(opening,s,5),false);
  assert.equal(canSubmitContext(opening,state('doctor'),5),false);assert.equal(canSubmitContext(opening,{...s,crew:'Crew 002'},4),false);
});
test('native synthetic session imports retain 8MiB compatibility and assistant tools keep their destination',()=>{
  assert.equal(importByteLimit('import_session'),8*1024*1024);assert.equal(importByteLimit('import_observations'),2*1024*1024);assert.equal(importByteLimit('import_signal'),2*1024*1024);
  assert.deepEqual(assistantToolTarget('signal'),{tab:'recordings',action:null});assert.equal(assistantToolTarget('reaction').action,'capture_reaction');assert.equal(assistantToolTarget('manual').action,'capture_manual');assert.equal(assistantToolTarget('unknown'),null);
});
test('non-local tabs offer direct session actions for crew without exposing crew controls to doctor',()=>{
  const guidedCrew={...state('crew'),mode:'guided',local:null,actions:{}},interactiveCrew={...state('crew'),mode:'operator',local:null,actions:{capture:true}},guidedDoctor={...state('doctor'),mode:'guided',local:null,actions:{}};
  const crewMeas=render(Measurements,{state:guidedCrew}),intMeas=render(Measurements,{state:interactiveCrew}),docMeas=render(Measurements,{state:guidedDoctor});
  assert.match(crewMeas,/Start recorded session/);assert.match(crewMeas,/Start interactive demo/);
  assert.match(intMeas,/Start recorded session/);assert.match(intMeas,/Capture synthetic measurement/);
  assert.doesNotMatch(docMeas,/Start recorded session|Capture synthetic measurement|Start interactive demo/);
  const crewAssist=render(Assistant,{state:guidedCrew}),docAssist=render(Assistant,{state:guidedDoctor});
  assert.match(crewAssist,/Start recorded session/);assert.doesNotMatch(docAssist,/Start recorded session/);
});

test('manual editor explains real local time and only prefills grounded matching equipment with confirmation',()=>{
  const s=state('crew');s.local.observations=[{metric:'heart_rate',unit:'bpm',value:74,quality:'usable',source_id:'LOCAL:recorded-1:wrist-monitor',method:'optical pulse',observed_at:'2026-11-15T09:00:00Z'}];
  const html=render(LocalEditor,{state:s,draft:{action:'capture_manual',payload:{metric:'heart_rate'},context:contextOf(s)}});assert.match(html,/value="wrist-monitor"/);assert.match(html,/value="optical pulse"/);assert.match(html,/confirm these describe the equipment/);assert.match(html,/simulated mission clock/);assert.match(html,/datetime-local/);
});
test('observation table orders acquisition time and gives signal or intake rows a record label',()=>{
  const html=render(ObservationTable,{rows:[{id:'new',metric:'heart_rate',value:80,unit:'bpm',observed_at:'2026-11-15T09:10:00Z'},{id:'old',metric:'heart_rate',value:74,unit:'bpm',observed_at:'2026-11-15T09:00:00Z'},{id:'signal',metric:'signal_review',value:null,unit:'status',observed_at:'2026-11-15T09:12:00Z'}]});assert.ok(html.indexOf('74 bpm')<html.indexOf('80 bpm'));assert.match(html,/Signal review record/);assert.doesNotMatch(html,/Unavailable status/);
});
