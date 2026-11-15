import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {unlink,readFile} from 'node:fs/promises';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {guideScenes,guideIndex,demoScenes,stepSelection,selectedStep,completionReason,stepTitle,stepKeyIndex} from '../src/guide.mjs';
const compiled=new URL('../.test-guide.mjs',import.meta.url);
await build({entryPoints:[new URL('../src/guide.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],outfile:compiled.pathname.replace(/^\/([A-Za-z]:)/,'$1'),bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic'});
const {QuickGuide,GuideIllustration,SelfCheck,GuidedTimeline}=await import(compiled.href);await unlink(compiled);
const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
const makeState=()=>({role:'crew',crew:'Crew 001',crews:[{id:'Crew 001'},{id:'Crew 002'}],mode:'local',session_id:'recorded-one',revision:4,snapshot_digest:'digest-one',actions:{complete:true,followup:false},projection:{snapshot_at:'2026-11-15T09:40:00Z',metric:{value:585,unit:'ms',quality:'usable',observed_at:'2026-11-15T09:30:00Z',history:[{value:420,quality:'usable',observed_at:'2026-11-15T09:00:00Z'},{value:585,quality:'usable',observed_at:'2026-11-15T09:30:00Z'}],reference:{status:'available',count:5,mean:421}},checklist:{branch:'B',steps:['Read the actual received instruction.','Repeat the supplied measurement.'],completed_steps:[0],completion_count:1,total_steps:2,stale_completions:[]}},local:{assistant:{handoff:{records:[]}}}});

test('guide is exactly four finite manual scenes and renders without mutating session or dispatching actions',()=>{
  const s=makeState(),before=structuredClone(s);let actions=0;
  assert.equal(guideScenes.length,4);assert.equal(guideIndex(-4),0);assert.equal(guideIndex(99),3);assert.equal(guideIndex(NaN),0);
  const markup=render(QuickGuide,{state:s,onClose:()=>actions++,onDemo:()=>actions++});
  assert.match(markup,/Quick guide/);assert.match(markup,/Close guide/);assert.match(markup,/Show demo controls/);assert.match(markup,/Back/);assert.match(markup,/Next/);assert.match(markup,/human-figure/);
  for(let index=0;index<guideScenes.length;index++)render(GuideIllustration,{index,state:s});
  assert.deepEqual(s,before);assert.equal(actions,0);
});

test('history guide uses supplied comparable observations and actual reference, while delay guide never invents onboard doctor values',()=>{
  const s=makeState(),history=render(GuideIllustration,{index:1,state:s});assert.match(history,/585/);assert.match(history,/5 earlier observations/);assert.match(history,/421 ms mean/);assert.match(history,/polyline/);
  s.projection.metric.history=[];assert.match(render(GuideIllustration,{index:1,state:s}),/No usable observations/);
  s.role='doctor';const delay=render(GuideIllustration,{index:3,state:s});assert.match(delay,/Current onboard evidence is not visible here/);assert.equal((delay.match(/585/g)||[]).length,1);
});

test('selected received step clamps, defaults to next incomplete, and resets across evidence and checklist contexts',()=>{
  const s=makeState();assert.equal(selectedStep(s,null),1);assert.equal(stepSelection(s,99).index,1);assert.equal(stepSelection(s,-1).index,0);
  const selected=stepSelection(s,0);assert.equal(selectedStep(s,selected),0);
  for(const change of [{role:'doctor'},{crew:'Crew 002'},{session_id:'another'},{revision:5},{snapshot_digest:'new'}])assert.equal(selectedStep({...s,...change},selected),1);
  const changed=structuredClone(s);changed.projection.checklist.branch='A';assert.equal(selectedStep(changed,selected),1);
  changed.projection.checklist.steps=[];assert.equal(selectedStep(changed,selected),null);assert.equal(stepSelection(changed,3).index,null);
});

test('completion is gated by role, mode, busy, actual action gate and completed status',()=>{
  const s=makeState();assert.equal(completionReason(s,1),'');assert.match(completionReason(s,0),/already recorded/);assert.match(completionReason(s,1,true),/Please wait/);
  assert.match(completionReason({...s,role:'doctor'},1),/read only/);assert.match(completionReason({...s,mode:'guided'},1),/guided fixture/);assert.match(completionReason({...s,actions:{complete:false}},1),/No applicable/);
});

test('self-check leads with literal received instruction, explicit completion and state-specific warnings',()=>{
  const s=makeState(),before=structuredClone(s);let calls=0;const markup=render(SelfCheck,{state:s,onAction:()=>calls++});assert.match(markup,/Repeat the supplied measurement/);assert.match(markup,/aria-current="step"/);assert.match(markup,/Record completion/);assert.match(markup,/Send follow-up/);assert.equal(calls,0);assert.deepEqual(s,before);
  s.mode='guided';s.projection.checklist.stale_completions=[{step:0}];const guided=render(SelfCheck,{state:s});assert.match(guided,/Synthetic guided fixture/);assert.match(guided,/Prior completions are retained for older evidence/);assert.match(guided,/disabled=""[^>]*aria-describedby="completion-unavailable"/);
  s.role='doctor';const doctor=render(SelfCheck,{state:s});assert.match(doctor,/Flight-surgeon view is read only/);assert.doesNotMatch(doctor,/Send follow-up/);
});

test('timeline is explicitly synthetic and finite; opening the guide cannot seek or reset a session',async()=>{
  assert.deepEqual(demoScenes.map(s=>s.seconds),[0,1200,1800,2400]);const markup=render(GuidedTimeline,{scene:3,onSeek:()=>assert.fail('render must not seek')});assert.match(markup,/Synthetic/);assert.match(markup,/09:40/);assert.equal((markup.match(/aria-pressed="true"/g)||[]).length,1);
  const source=await readFile(new URL('../src/guide.jsx',import.meta.url),'utf8'),guide=source.slice(source.indexOf('export function QuickGuide'),source.indexOf('export function GuidedTimeline'));
  assert.doesNotMatch(guide,/submit\(|onAction|reset_guided|setInterval|setTimeout/);
});
test('step navigation presents literal concise titles and supports bounded keyboard movement',()=>{
  assert.equal(stepTitle('Repeat the measured heart rate.'),'Repeat the measured heart rate.');assert.equal(stepTitle('x'.repeat(100)).length,64);
  assert.equal(stepKeyIndex(0,3,'ArrowUp'),0);assert.equal(stepKeyIndex(2,3,'ArrowRight'),2);assert.equal(stepKeyIndex(1,3,'Home'),0);assert.equal(stepKeyIndex(1,3,'End'),2);assert.equal(stepKeyIndex(0,0,'ArrowDown'),null);assert.equal(stepKeyIndex(0,3,'Enter'),null);
  const s=makeState(),markup=render(SelfCheck,{state:s});assert.match(markup,/Read the actual received instruction/);assert.match(markup,/Repeat measurement/);
});
test('zero-step UNKNOWN retains actual dependency comparison and repeat acquisition',()=>{
  const s=makeState();s.projection.checklist={branch:'UNKNOWN',steps:[],completed_steps:[],completion_count:0,stale_completions:[]};s.projection.protocol_evidence={dependencies:[{metric:'temperature',unit:'C',reviewed:{value:36.5,unit:'C',usable:true},current:{value:null,usable:false,reason:'missing dependency'}}]};
  const markup=render(SelfCheck,{state:s});assert.match(markup,/Checklist needs current evidence/);assert.match(markup,/temperature/);assert.match(markup,/missing dependency/);assert.match(markup,/Repeat measurement/);assert.doesNotMatch(markup,/Record completion<\/button>/);
});

test('history guide names the actual available heart-rate metric throughout its caption and chart',()=>{
  const s=makeState();s.projection.metric={value:null,quality:'missing',history:[]};s.local.observations=[{metric:'heart_rate',value:80,unit:'bpm',quality:'usable',observed_at:'2026-11-15T09:10:00Z',source_id:'wrist',method:'optical',subject_id:'sub',phase:'rest'}];
  const html=render(GuideIllustration,{index:1,state:s});assert.match(html,/Heart rate/);assert.match(html,/80/);assert.match(html,/bpm/);assert.doesNotMatch(html,/Reaction time/);assert.match(html,/comparable Heart rate history/);
});
