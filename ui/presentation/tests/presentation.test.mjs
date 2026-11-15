import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,unlink} from 'node:fs/promises';
import {build} from 'esbuild';
import {contextOf,sameContext,ResponseGate,chartGeometry,metricPoints,validateDraft,authorTemplate,protocolGates,inspectSignalFeatures,buildSampleSignal,cleanSourceLabel,validateImportFile,time,fullTime,actionReason,connectionMessage} from '../src/model.mjs';
import {preferredMetric} from '../src/monitor.mjs';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const compiled=new URL('../.test-components.mjs',import.meta.url);
await build({entryPoints:[new URL('../src/components.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],outfile:compiled.pathname.replace(/^\/([A-Za-z]:)/,'$1'),bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic'});
const {CrewDetail,DoctorCrewDetail,CrewList,Transcript,Chart,Timestamp,ActionButton,EvidenceComparison,WorkflowJourney}=await import(compiled.href);
await unlink(compiled);
// Standalone fixtures here derive from the actual role projection contract.
const state=(role,value)=>({role,crew:'Crew 001',session_id:'test',revision:1,mode:'guided',crews:[{id:'Crew 001',value,unit:'ms',status:value===null?'Measurement needed':'Monitoring'}],messages:[],transport:{packets:[],seconds_until_arrival:null},actions:{},projection:{status:value===null?'Measurement needed':'Monitoring',unknown:value===null,metric:{value,unit:'ms',quality:value===null?'missing':'usable',observed_at:null,history:[],reference:{status:'unavailable'}},checklist:{steps:[],completed_steps:[],reasons:[]},sensors:{},digest:'one'}});
test('old GET and POST responses cannot cross a role or crew navigation',()=>{const g=new ResponseGate();const crewEpoch=g.navigate();assert.equal(g.accept(crewEpoch,{revision:2}),true);const doctorEpoch=g.navigate();assert.equal(g.accept(crewEpoch,{revision:999}),false);assert.equal(g.accept(doctorEpoch,{revision:3}),true);assert.equal(g.accept(doctorEpoch,{revision:2}),false);g.navigate();assert.equal(g.accept(doctorEpoch,{revision:4}),false);});
test('polling does not rebind the draft revision, evidence, crew, role or session',()=>{const s=state('crew',585),opening=contextOf(s);for(const changed of [{...s,revision:2},{...s,session_id:'new'},{...s,crew:'Crew 002'},{...s,role:'doctor'},{...s,projection:{...s.projection,digest:'different'}}])assert.equal(sameContext(opening,contextOf(changed)),false);assert.equal(sameContext(opening,contextOf(s)),true);assert.equal(opening.revision,1);});
test('role detail and roster render only the supplied role evidence',()=>{const crew=state('crew',585),doctor=state('doctor',420);const c=renderToStaticMarkup(React.createElement(CrewDetail,{state:crew}));const d=renderToStaticMarkup(React.createElement(DoctorCrewDetail,{state:doctor}));assert.match(c,/585/);assert.doesNotMatch(d,/585/);assert.match(d,/420/);assert.match(d,/Earth \/ received snapshot/);const roster=renderToStaticMarkup(React.createElement(CrewList,{state:doctor,selected:'Crew 001'}));assert.match(roster,/420 ms/);assert.doesNotMatch(roster,/585/);});
test('unavailable observations remain explicit and no messages are invented',()=>{const doctor=state('doctor',null);const markup=renderToStaticMarkup(React.createElement(DoctorCrewDetail,{state:doctor}));assert.match(markup,/Unavailable/);assert.match(markup,/condition unknown/);assert.doesNotMatch(markup,/condition neutral/);assert.match(renderToStaticMarkup(React.createElement(Transcript,{state:doctor})),/No messages visible/);});
test('null and review quality samples split SVG paths instead of connecting gaps',()=>{const points=[{t:100,value:1},{t:101,value:null},{t:102,value:2},{t:103,value:3}];const g=chartGeometry(points);assert.equal(g.segments.length,2);assert.equal(g.start,100);assert.equal(g.end,103);const svg=renderToStaticMarkup(React.createElement(Chart,{points,source:true,label:'Separate NASA source recording'}));assert.equal((svg.match(/<polyline/g)||[]).length,2);assert.match(svg,/100.000 source s/);assert.equal(metricPoints({history:[{observed_at:'2026-11-14T09:00:00Z',quality:'review',value:585}]})[0].value,null);});
test('author checklist payload uses bounded newline strings and completion requires note',()=>{const p={operator:'ge',threshold:'500',original_reply:'Review measurement',checklist_a:'Check device\nRepeat measurement',checklist_b:'Record task context'};assert.equal(validateDraft('author_reply',p),null);for(const bad of [{operator:'eq'},{threshold:'Infinity'},{threshold:'10001'},{checklist_a:'\n'},{checklist_b:Array(7).fill('step').join('\n')},{original_reply:'x'.repeat(2001)}])assert.ok(validateDraft('author_reply',{...p,...bad}));assert.ok(validateDraft('complete',{note:' '}));assert.equal(validateDraft('complete',{note:'Recorded task context'}),null);assert.ok(validateDraft('capture',{value:''}));assert.ok(validateDraft('capture',{value:'NaN'}));assert.equal(validateDraft('capture',{value:'0'}),null);});
test('launch fragment supports reload without persistent browser credential storage',async()=>{const source=await readFile(new URL('../src/app.jsx',import.meta.url),'utf8');assert.match(source,/location.hash.slice\(1\)/);assert.doesNotMatch(source,/sessionStorage|localStorage/);assert.match(source,/new URLSearchParams\(\{token,role:r\}\)/);assert.match(source,/history\.replaceState\(null,'','#'\+new URLSearchParams\(\{token,role:nextRole\}\)\)/);});

test('concise UTC clocks retain exact accessible observation timestamps',()=>{
  const value='2026-11-14T09:30:12Z';
  assert.equal(time(value),'09:30 UTC');
  assert.equal(fullTime(value),'2026-11-14 09:30:12 UTC');
  assert.equal(time(null),'Unavailable');
  assert.equal(time('invalid'),'Unavailable');
  const markup=renderToStaticMarkup(React.createElement(Timestamp,{value}));
  assert.match(markup,/dateTime="2026-11-14T09:30:12Z"/i);
  assert.match(markup,/aria-label="2026-11-14 09:30:12 UTC"/);
});
test('chart ticks represent the actual padded domain and singleton source samples stay visible',()=>{
  const points=[{t:0,value:420},{t:1,value:null},{t:2,value:585}];
  const geometry=chartGeometry(points);
  assert.equal(geometry.ticks[0].value,geometry.domainMax);
  assert.equal(geometry.ticks[2].value,geometry.domainMin);
  const tickY=value=>14+(geometry.domainMax-value)/(geometry.domainMax-geometry.domainMin)*138;
  for(const tick of geometry.ticks)assert.ok(Math.abs(tick.y-tickY(tick.value))<1e-10);
  for(let i=0;i<2;i++)assert.ok(Math.abs(geometry.segments[i][0][1]-tickY(points[i*2].value))<1e-10);
  const markup=renderToStaticMarkup(React.createElement(Chart,{points,source:true,label:'Separate source'}));
  assert.equal((markup.match(/<circle/g)||[]).length,2);
  assert.equal((markup.match(/<polyline/g)||[]).length,2);
});
test('unavailable workflow controls expose prerequisite reasons and busy feedback',()=>{
  const s=state('doctor',null);
  assert.match(actionReason(s,'author_reply'),/consultation must arrive/);
  s.actions.author_reply=true;
  assert.equal(actionReason(s,'author_reply'),'');
  assert.match(actionReason(s,'author_reply',true),/Please wait/);
  s.actions.author_reply=false;
  const markup=renderToStaticMarkup(React.createElement(ActionButton,{state:s,action:'author_reply'},'Author checklist'));
  assert.match(markup,/disabled/);
  assert.match(markup,/aria-describedby="reason-author_reply"/);
  assert.match(markup,/consultation must arrive/);
});
test('host failures distinguish expired sessions from retryable connection problems',()=>{
  for(const status of [401,403])assert.match(connectionMessage({status,message:'restricted'}),/Reopen BioTwin Sync/);
  assert.match(connectionMessage(new Error('Failed to fetch')),/Keep BioTwin Sync open, then retry/);
  assert.ok(validateDraft('capture',{value:' '}));
  assert.match(validateDraft('complete',{note:''}),/1 to 2000/);
});

test('chart scale uses HTML text outside the shrinking SVG viewport',()=>{
  const markup=renderToStaticMarkup(React.createElement(Chart,{points:[{t:0,value:420},{t:1,value:585}],label:'Reaction time'}));
  assert.equal((markup.match(/class="chart-tick chart-tick-/g)||[]).length,3);
  assert.match(markup,/preserveAspectRatio="none"/);
  assert.doesNotMatch(markup,/<text/);
  assert.match(markup,/class="chart-y-axis" aria-label="Vertical scale"/);
});

test('descriptive bands use backend mean/sample SD and the plotted domain contains both bands',()=>{
  const points=[{t:0,value:100},{t:1,value:null},{t:2,value:101}],reference={status:'available',mean:90,sd:20,count:5};
  const g=chartGeometry(points,720,180,reference);
  assert.ok(g.domainMin<50);assert.ok(g.domainMax>130);assert.equal(g.reference.bands.length,2);assert.equal(g.segments.length,2);
  const y=v=>14+(g.domainMax-v)/(g.domainMax-g.domainMin)*138;
  assert.equal(g.reference.mean,y(90));assert.equal(g.reference.bands[0].top,y(130));assert.equal(g.reference.bands[0].bottom,y(50));
  const markup=renderToStaticMarkup(React.createElement(Chart,{points,reference,label:'Descriptive reaction history'}));
  assert.equal((markup.match(/<rect/g)||[]).length,2);assert.match(markup,/not a health range/);assert.match(markup,/Earlier mean/);
  const source=renderToStaticMarkup(React.createElement(Chart,{points,reference,source:true,label:'Source'}));assert.doesNotMatch(source,/reference-band/);
});
test('unavailable references cannot manufacture bands and event timestamps share the observation axis',()=>{
  const points=[{t:100,value:2},{t:200,value:4}],events=[{t:50,label:'Crew packet sent'},{t:250,label:'Earth received'}];
  for(const reference of [null,{status:'unavailable',mean:10,sd:3},{status:'available',mean:NaN,sd:3}])assert.equal(chartGeometry(points,720,180,reference).reference,null);
  const g=chartGeometry(points,720,180,null,events);assert.equal(g.start,50);assert.equal(g.end,250);assert.equal(g.events[0].x,52);assert.equal(g.events[1].x,704);
  assert.ok(g.segments[0][0][0]>52);assert.ok(g.segments[0][1][0]<704);
  const empty=chartGeometry([{t:1,value:null}],720,180,{status:'available',mean:10,sd:3},events);assert.deepEqual(empty.segments,[]);
});
test('protocol comparison displays its actual dependency and unusable context without borrowing reaction values',()=>{
  const s=state('crew',585);s.projection.reviewed={value:420,unit:'ms'};s.projection.protocol_evidence={protocol_id:'hr-review',reviewed_snapshot_at:'2026-11-15T09:00:00Z',dependencies:[{metric:'heart_rate',unit:'bpm',current:{value:92,unit:'bpm',source_id:'device-new',method:'manual',observed_at:'2026-11-15T09:30:00Z',usable:false,reason:'incompatible dependency'},reviewed:{value:72,unit:'bpm',source_id:'device-a',method:'manual',observed_at:'2026-11-15T09:00:00Z',usable:true}}]};
  const markup=renderToStaticMarkup(React.createElement(EvidenceComparison,{state:s,selected:'reaction_time',onSelect:()=>{}}));assert.match(markup,/heart rate/);assert.match(markup,/92 bpm/);assert.match(markup,/72 bpm/);assert.match(markup,/incompatible dependency/);assert.match(markup,/device-new/);assert.match(markup,/View on monitor/);assert.doesNotMatch(markup,/585|420|ms/);
  const active=renderToStaticMarkup(React.createElement(EvidenceComparison,{state:s,selected:'heart_rate',onSelect:()=>{}}));assert.doesNotMatch(active,/View on monitor/);
  s.role='doctor';const doctor=renderToStaticMarkup(React.createElement(EvidenceComparison,{state:s}));assert.match(doctor,/Delivered now/);assert.doesNotMatch(doctor,/Onboard now/);
});
test('authorTemplate supplies valid conditional checklist templates across supported metrics',()=>{
  for(const metric of ['reaction_time','heart_rate','spo2','temperature','sleep_duration','systolic_bp']){
    const tpl=authorTemplate(metric);
    assert.equal(validateDraft('author_reply',tpl),null);
  }
});
test('protocolGates and inspectSignalFeatures distinguish clean vs corrupted telemetry and context mismatches',()=>{
  const clean=buildSampleSignal('LOCAL:crew-001','2026-11-15T09:00:00Z','clean');
  const corrupted=buildSampleSignal('LOCAL:crew-001','2026-11-15T09:00:00Z','corrupted');
  const cleanInfo=inspectSignalFeatures(clean.samples.map((v,i)=>({t:i/100,value:v})));
  const badInfo=inspectSignalFeatures(corrupted.samples.map((v,i)=>({t:i/100,value:v})));
  assert.equal(cleanInfo.flagged,false);
  assert.equal(badInfo.flagged,true);
  assert.ok(badInfo.flatPct>10);
  const okGates=protocolGates({reviewed:{source_id:'dev-1',method:'manual',observed_at:'2026-11-15T09:00:00Z'},current:{source_id:'dev-1',method:'manual',observed_at:'2026-11-15T09:10:00Z',usable:true}},'8f3a9bc123456789');
  assert.equal(okGates.every(g=>g.ok),true);
  const failGates=protocolGates({reviewed:{source_id:'dev-1',method:'manual',observed_at:'2026-11-15T09:00:00Z'},current:{source_id:'dev-2',method:'manual',observed_at:'2026-11-15T09:10:00Z',usable:false,reason:'incompatible dependency'}},'8f3a9bc123456789');
  assert.equal(failGates[0].ok,false);
  assert.equal(failGates[1].ok,false);
});
test('cleanSourceLabel, validateImportFile and preferredMetric preserve context and provide specific CSV errors',()=>{
  assert.equal(cleanSourceLabel('LOCAL:81fa1487a1cb4cc0:wrist-monitor'),'wrist-monitor');
  assert.equal(cleanSourceLabel('device-new'),'device-new');
  assert.match(validateImportFile('import_observations','bad.csv','metric,value\nheart_rate,74','LOCAL:1:crew-001'),/missing required column\(s\): subject_id/);
  assert.match(validateImportFile('import_observations','bad.csv','subject_id,metric,value,unit,observed_at,source_id,method,quality\nwrong,heart_rate,74,bpm,2026-11-15T09:00:00Z,dev,manual,usable','LOCAL:1:crew-001'),/does not match this crew session/);
  assert.equal(validateImportFile('import_observations','ok.csv','subject_id,metric,value,unit,observed_at,source_id,method,quality\nLOCAL:1:crew-001,heart_rate,74,bpm,2026-11-15T09:00:00Z,dev,manual,usable','LOCAL:1:crew-001'),null);
  const s=state('crew',null);s.mode='local';s.local={observations:[{id:'1',subject_id:'sub',metric:'heart_rate',value:74,unit:'bpm',phase:'rest',method:'manual',source_id:'dev',quality:'usable',observed_at:'2026-11-15T09:10:00Z'}]};
  assert.equal(preferredMetric(s,'reaction_time'),'heart_rate');
  const bad={operator:'ge',threshold:'',original_reply:'x',checklist_a:'a',checklist_b:'b'};
  assert.match(validateDraft('author_reply',{...bad,metric:'heart_rate'}),/bpm/);assert.match(validateDraft('author_reply',bad),/ms/);
});

test('consultation and roster use the real selected heart-rate context and keep other crews separate',()=>{
  const s=state('doctor',null);s.mode='local';s.local={observations:[{subject_id:'sub',source_id:'LOCAL:test:wrist-monitor',metric:'heart_rate',unit:'bpm',method:'optical pulse',phase:'local',value:74,quality:'usable',observed_at:'2026-11-15T09:00:00Z'},{subject_id:'sub',source_id:'LOCAL:test:wrist-monitor',metric:'heart_rate',unit:'bpm',method:'optical pulse',phase:'local',value:80,quality:'usable',observed_at:'2026-11-15T09:10:00Z'}]};s.crews.push({id:'Crew 002',value:null});
  const detail=renderToStaticMarkup(React.createElement(DoctorCrewDetail,{state:s,selected:'heart_rate'}));assert.match(detail,/Heart rate/);assert.match(detail,/80<span>bpm/);assert.match(detail,/wrist-monitor/);assert.match(detail,/optical pulse/);assert.match(detail,/received Heart rate observations/);assert.doesNotMatch(detail,/Measurement needed|Reaction time only|reaction-time observations/);assert.equal((detail.match(/class="chart-dot"/g)||[]).length,2);
  const roster=renderToStaticMarkup(React.createElement(CrewList,{state:s,selected:'Crew 001'}));assert.match(roster,/Heart rate 80 bpm/);assert.match(roster,/Select to review separate evidence/);assert.equal((roster.match(/80 bpm/g)||[]).length,1);
});
test('journey shows missing protocol evidence and does not manufacture passed checks',()=>{
  const s=state('crew',null),html=renderToStaticMarkup(React.createElement(WorkflowJourney,{state:s}));assert.match(html,/Four-step measurement journey/);assert.match(html,/Try this workflow interactively/);assert.match(html,/No protocol evidence/);assert.match(html,/does not authenticate the clinician/);assert.doesNotMatch(html,/Freshness check passed|hash verified/);
});

test('doctor dependency comparison cannot claim an onboard branch when a fresh delivered reading has no evaluation',()=>{
  const s=state('doctor',null);s.projection.protocol_evidence={protocol_id:'earlier-rule',reviewed_digest:'earlier',dependencies:[{metric:'heart_rate',unit:'bpm',reviewed:{value:74,unit:'bpm',usable:true,source_id:'wrist',method:'optical'},current:{value:85,unit:'bpm',usable:true,compatible:true,source_id:'wrist',method:'optical'}}]};s.projection.checklist.branch=null;
  let html=renderToStaticMarkup(React.createElement(EvidenceComparison,{state:s}));assert.match(html,/74 bpm/);assert.match(html,/85 bpm/);assert.match(html,/Awaiting onboard evaluation/);assert.match(html,/Review delivered evidence/);assert.doesNotMatch(html,/Updated for latest reading|evaluated onboard|automatically selected|selected instruction outcome|Why this instruction is active/);
  s.role='crew';s.projection.checklist.branch='A';s.projection.checklist.steps=['Record repeat reading'];html=renderToStaticMarkup(React.createElement(EvidenceComparison,{state:s}));assert.match(html,/Updated for latest reading/);assert.match(html,/selected instruction outcome A/);assert.match(html,/protocol branch A/);
  s.projection.checklist.steps=[];html=renderToStaticMarkup(React.createElement(EvidenceComparison,{state:s}));assert.doesNotMatch(html,/Updated for latest reading|selected instruction outcome/);
});

test('compact and expanded technology views agree on waiting, held and selected outcomes',()=>{
  const s=state('doctor',null);s.mode='local';s.projection.protocol_evidence={dependencies:[{metric:'heart_rate',unit:'bpm',reviewed:{value:74,unit:'bpm',source_id:'wrist',method:'optical'},current:{value:80,unit:'bpm',source_id:'wrist',method:'optical',compatible:true,usable:true,quality:'usable'}}]};s.projection.checklist.branch=null;
  const views=()=>[renderToStaticMarkup(React.createElement(WorkflowJourney,{state:s})),renderToStaticMarkup(React.createElement(EvidenceComparison,{state:s}))];
  for(const html of views()){assert.match(html,/Awaiting onboard evaluation/);assert.doesNotMatch(html,/Rule held|Updated for latest reading|selected instruction outcome/);}
  s.projection.checklist.branch='A';s.projection.checklist.steps=['Record reading'];for(const html of views())assert.match(html,/selected instruction outcome A/);
  s.projection.checklist.steps=[];for(const html of views())assert.doesNotMatch(html,/selected instruction outcome|Updated for latest reading/);
  s.projection.checklist.branch='UNKNOWN';for(const html of views())assert.match(html,/held|UNKNOWN/);
  s.projection.checklist.branch=null;s.projection.protocol_evidence.dependencies[0].current.usable=false;for(const html of views())assert.match(html,/held|Paused/);
});
