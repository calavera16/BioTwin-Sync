import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {unlink} from 'node:fs/promises';
import {build} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {phases,storyAt,advanceStory,boundedTime,sourceTrace,sourceRequest,missionProofView,exampleCheckView,exampleDialogKey} from '../src/mission.mjs';
const compiled=new URL('../.test-mission.mjs',import.meta.url);
await build({entryPoints:[new URL('../src/mission.jsx',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')],outfile:compiled.pathname.replace(/^\/([A-Za-z]:)/,'$1'),bundle:true,platform:'node',format:'esm',packages:'external',loader:{'.css':'empty'},jsx:'automatic'});
const {Mission,MissionProof,EvidenceChecks,ExampleCheckResult}=await import(compiled.href);await unlink(compiled);

test('mission opening and completed state render without role, session or network mutation',()=>{
  let actions=0;
  const playback={seconds:0,playing:false},before=structuredClone(playback);
  const render=seconds=>renderToStaticMarkup(React.createElement(Mission,{api:()=>{actions++;throw Error('No request during render');},playback:{...playback,seconds},onPlayback:()=>actions++,onWorkspace:()=>actions++}));
  const opening=render(0);
  assert.match(opening,/Recheck delayed instructions/);
  assert.match(opening,/Watch 30-second example/);
  assert.match(opening,/Open workspace/);
  assert.match(opening,/ECG/);
  assert.match(opening,/Pulse waveform/);
  assert.doesNotMatch(opening,/No reading/);
  const finished=render(30);
  assert.match(finished,/Example complete/);
  assert.match(finished,/Reply arrived at \+40 min/);
  assert.doesNotMatch(finished,/Current checklist selected|Instruction updated|420 ms|585 ms/);
  assert.deepEqual(playback,before);
  assert.equal(actions,0);
  assert.match(finished,/Loading the isolated workflow computation/);
  assert.match(finished,/newer onboard evidence/);
});

function proofFixture() {
  return {phases:[0,1200,1800,2400].map((mission_seconds,i)=>({mission_seconds,
    doctor:{metric:{value:i?420:null}},crew:{metric:{value:i<2?420:585},digest:'current',reviewed:{digest:i===3?'reviewed':null},
      checklist:{branch:i===3?'B':null,steps:i===3?['Repeat the supplied measurement.','Send the current result.']:[],reasons:[]},
      protocol_evidence:i===3?{dependencies:[{metric:'reaction_time',reviewed:{value:420},current:{value:585,compatible:true,usable:true,source_id:'synthetic-task',method:'fixed',reason:null}}]}:null}}))};
}
test('computed evidence uses exact role-scoped values and actual checklist, without mutating proof',()=>{
  const proof=proofFixture(),before=structuredClone(proof);
  assert.equal(missionProofView(proof,0).phase.doctor.metric.value,null);
  assert.equal(missionProofView(proof,18.999).phase.crew.metric.value,420);
  assert.equal(missionProofView(proof,19).phase.crew.metric.value,585);
  assert.equal(missionProofView(proof,22.999).evaluated,false);
  const result=missionProofView(proof,23);
  assert.equal(result.evaluated,true);
  assert.equal(result.steps[0],proof.phases[3].crew.checklist.steps[0]);
  assert.deepEqual(result.gates.map(g=>g.state),['pass','pass']);
  const html=renderToStaticMarkup(React.createElement(MissionProof,{proof,seconds:30}));
  assert.match(html,/420 ms/);assert.match(html,/585 ms/);
  assert.match(html,/Repeat the supplied measurement\./);
  assert.match(html,/human-authored branch B/);
  assert.deepEqual(proof,before);
});
test('unavailable or malformed computation never borrows the scripted outcome',()=>{
  assert.equal(missionProofView(null,30),null);
  assert.equal(missionProofView({phases:[{}]},30),null);
  const html=renderToStaticMarkup(React.createElement(MissionProof,{proof:null,seconds:30,error:'Offline'}));
  assert.match(html,/Workflow proof unavailable/);
  assert.match(html,/Retry computation/);
  assert.doesNotMatch(html,/Current checklist selected|420 ms|585 ms|class="mission-gate pass"/);
});
test('missing and held evaluator evidence stay distinct and absent fields never pass',()=>{
  const proof=proofFixture(),last=proof.phases[3].crew;
  last.checklist={branch:'UNKNOWN',steps:[],reasons:['stale dependency']};
  last.protocol_evidence.dependencies[0].current={value:585,compatible:true,usable:false,reason:'stale dependency'};
  let result=missionProofView(proof,30);
  assert.equal(result.evaluated,false);
  assert.equal(result.outcome,'Instruction held');
  assert.deepEqual(result.gates.map(g=>g.state),['pass','held']);
  last.protocol_evidence.dependencies[0].current={value:null,compatible:false,usable:false,reason:'missing dependency'};
  assert.deepEqual(missionProofView(proof,30).gates.map(g=>g.state),['missing','missing']);
  last.protocol_evidence.dependencies[0].current={value:585};
  assert.deepEqual(missionProofView(proof,30).gates.map(g=>g.state),['missing','missing']);
  last.protocol_evidence.dependencies=[];
  const html=renderToStaticMarkup(React.createElement(MissionProof,{proof,seconds:30}));
  assert.match(html,/Evidence checks not returned/);
  assert.doesNotMatch(html,/Current checklist selected|class="mission-gate pass"/);
});

test('A/B checklist labels require complete returned dependencies and tolerate malformed entries',()=>{
  for(const branch of ['A','B']) {
    for(const dependencies of [[],[null],[null,proofFixture().phases[3].crew.protocol_evidence.dependencies[0]]]) {
      const proof=proofFixture(),last=proof.phases[3].crew;
      last.checklist.branch=branch;
      last.protocol_evidence.dependencies=dependencies;
      const before=structuredClone(proof),view=missionProofView(proof,30);
      assert.equal(view.evaluated,false,`${branch} with incomplete dependencies`);
      assert.equal(view.outcome,'Instruction evidence missing');
      const html=renderToStaticMarkup(React.createElement(MissionProof,{proof,seconds:30}));
      assert.doesNotMatch(html,/Current checklist selected|The evaluator selected human-authored branch/);
      assert.deepEqual(proof,before);
    }
    const proof=proofFixture(),last=proof.phases[3].crew;
    last.checklist.branch=branch;
    assert.equal(missionProofView(proof,30).evaluated,true,`${branch} with usable returned evidence`);
    last.protocol_evidence.dependencies[0].current.usable=false;
    last.protocol_evidence.dependencies[0].current.reason='unusable dependency';
    assert.equal(missionProofView(proof,30).evaluated,false);
    assert.equal(missionProofView(proof,30).outcome,'Instruction held');
    const held=renderToStaticMarkup(React.createElement(MissionProof,{proof,seconds:30}));
    assert.match(held,/Reading needs review/);
    assert.doesNotMatch(held,/Current checklist selected|The evaluator selected human-authored branch/);
    last.protocol_evidence.dependencies[0].current.usable=true;
    last.protocol_evidence.dependencies[0].reviewed=null;
    assert.equal(missionProofView(proof,30).evaluated,false);
    assert.match(renderToStaticMarkup(React.createElement(MissionProof,{proof,seconds:30})),/Reviewed evidence missing/);
  }
});

test('story changes beats at exact compressed communication boundaries',()=>{
  assert.deepEqual(phases.map(p=>p.start),[0,6,14,23]);
  assert.equal(storyAt(5.999).phase,0);
  assert.equal(storyAt(6).phase,1);
  assert.equal(storyAt(13.999).received,false);
  assert.equal(storyAt(14).received,true);
  assert.equal(storyAt(14).outbound,1);
  assert.equal(storyAt(22.999).replied,false);
  assert.equal(storyAt(23).replied,true);
  assert.equal(storyAt(23).inbound,1);
  assert.equal(storyAt(30).complete,true);
  assert.equal(storyAt(99).seconds,30);
});
test('pause, resume and replay remain bounded and final frame persists',()=>{
  assert.equal(advanceStory(12,10,false),12);
  assert.equal(advanceStory(12,2,true),14);
  assert.equal(advanceStory(29.9,5,true),30);
  assert.equal(advanceStory(30,5,true),30);
  assert.equal(storyAt(0).phase,0);
  assert.equal(boundedTime(-5),0);
  assert.equal(boundedTime(NaN),0);
});
test('real source seconds control spacing, null samples and omitted intervals break paths',()=>{
  const points=[{t:700.1,value:1},{t:700.2,value:3},{t:700.3,value:null},{t:700.4,value:2},{t:700.5,value:1},{t:701.5,value:3}];
  const original=structuredClone(points),trace=sourceTrace(points);
  assert.equal(trace.start,700.1);
  assert.equal(trace.end,701.5);
  assert.equal((trace.path.match(/M/g)||[]).length,3);
  assert.match(trace.path,/M16.00,/);
  assert.match(trace.path,/M984.00,/);
  assert.deepEqual(points,original);
  assert.equal(sourceTrace([{t:1,value:null}]).path,'');
  assert.equal(sourceTrace([]).path,'');
  assert.doesNotMatch(sourceTrace([{t:1,value:4},{t:2,value:4}]).path,/NaN|Infinity/);
});
test('mission source requests are read only and have no session or role binding',()=>{
  assert.equal(sourceRequest('crew001_ecg.json',10),'/api/source?example=crew001_ecg.json&start=10');
  assert.equal(sourceRequest('crew001_bvp.json',99),'/api/source?example=crew001_bvp.json&start=50');
  const source=readFileSync(new URL('../src/mission.jsx',import.meta.url),'utf8');
  assert.doesNotMatch(source,/\/api\/(action|import|state)|localStorage|sessionStorage|Date\.parse|new Date\(/);
  assert.match(source,/controller\.abort\(\)/);
  assert.match(source,/cancelAnimationFrame\(id\)/);
  assert.match(source,/document.hidden/);
});


function exampleFixture() {
  const protocol={freshness_seconds:3600,conditions:{A:[{metric:'reaction_time',op:'le',value:448.2842712474619}],B:[{metric:'reaction_time',op:'gt',value:448.2842712474619}]},checklists:{A:['Actual A step'],B:['Actual B step'],UNKNOWN:['Acquire supported evidence for review']}};
  return {examples:{protocol,cases:[['below',420,'A',600],['above',585,'B',600],['stale',585,'UNKNOWN',3601]].map(([id,value,outcome,age])=>({id,measurement_age_seconds:age,evaluation:{outcome,checklist:protocol.checklists[outcome],reasons:[outcome==='UNKNOWN'?'current: stale dependency reaction_time':`only branch ${outcome} conditions met`]},evidence:{dependencies:[{unit:'ms',reviewed:{value:420,unit:'ms',compatible:true,usable:true},current:{value,unit:'ms',source_id:'SYNTHETIC:reaction-demo',method:'synthetic fixed reaction task',compatible:true,usable:outcome!=='UNKNOWN',reason:outcome==='UNKNOWN'?'stale dependency':null}}]}}))}};
}

test('optional evidence checks show the same exact rule and actual outcomes without mutation',()=>{
  const proof=exampleFixture(),before=structuredClone(proof);
  for(const [id,branch] of [['below','A'],['above','B'],['stale','UNKNOWN']]) {
    const view=exampleCheckView(proof,id);
    assert.equal(view.evaluation.outcome,branch);
    assert.equal(view.selected,branch!=='UNKNOWN');
    const html=renderToStaticMarkup(React.createElement(ExampleCheckResult,{view}));
    assert.match(html,/448.2842712474619/);assert.match(html,/448.28/);
    assert.match(html,/<details><summary>Exact stored condition &amp; timing<\/summary>/);
    assert.match(html,/Displayed cutoff rounded; exact rule below/);
    assert.doesNotMatch(html,/for speech/);
    assert.match(html,/420 ms/);assert.match(html,/allowed 3600 s/);
    assert.match(html,/SYNTHETIC:reaction-demo/);
    if(branch==='UNKNOWN') {
      assert.match(html,/3601 s/);assert.match(html,/stale dependency/);
      assert.match(html,/60 min 1 s \/ limit 60 min/);
      assert.match(html,/No active A\/B instructions/);
      assert.match(html,/request reviewer review/);
      assert.doesNotMatch(html,/Actual A step|Actual B step|Evaluator selected branch/);
    } else {
      assert.match(html,new RegExp(`Evaluator selected branch ${branch}`));
      assert.match(html,/10 min \/ limit 60 min/);
    }
  }
  const html=renderToStaticMarkup(React.createElement(EvidenceChecks,{proof,onClose:()=>{}}));
  assert.match(html,/aria-labelledby="example-check-title"/);
  assert.match(html,/Close example checks/);assert.match(html,/aria-pressed="true"/);
  assert.match(html,/do not demonstrate clinical benefit/);
  assert.deepEqual(proof,before);
});

test('example missing payload and stale successful branch fail closed',()=>{
  for(const proof of [null,{}, {examples:{cases:{}}}, {examples:{cases:[{}]}}]) assert.equal(exampleCheckView(proof),null);
  const unavailable=renderToStaticMarkup(React.createElement(EvidenceChecks,{proof:null,onClose:()=>{}}));
  assert.match(unavailable,/Example evidence unavailable/);assert.doesNotMatch(unavailable,/Evaluator selected branch/);
  const proof=exampleFixture();
  const stale=proof.examples.cases[2];stale.evaluation={outcome:'B',checklist:proof.examples.protocol.checklists.B,reasons:['only branch B conditions met']};
  stale.evidence.dependencies[0].current.usable=true;stale.evidence.dependencies[0].current.reason=null;
  const view=exampleCheckView(proof,'stale');assert.equal(view.selected,false);assert.deepEqual(view.steps,[]);
  const html=renderToStaticMarkup(React.createElement(ExampleCheckResult,{view}));
  assert.doesNotMatch(html,/Actual B step|Evaluator selected branch B/);
  proof.examples.cases[0].evidence.dependencies[0].reviewed.usable=false;
  assert.equal(exampleCheckView(proof,'below').selected,false);
});

test('example keyboard trap wraps both directions and Escape closes',()=>{
  assert.equal(exampleDialogKey('Tab',false,3,4),0);
  assert.equal(exampleDialogKey('Tab',true,0,4),3);
  assert.equal(exampleDialogKey('Tab',false,1,4),null);
  assert.equal(exampleDialogKey('Escape',false,1,4),'close');
  const source=readFileSync(new URL('../src/mission.jsx',import.meta.url),'utf8');
  assert.match(source,/element.showModal\(\)/);
  assert.match(source,/trigger\?\.current\?\.focus\(\)/);
  assert.match(source,/playing:false,sourcePlaying:false/);
});
