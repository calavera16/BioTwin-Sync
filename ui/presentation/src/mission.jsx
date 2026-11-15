import React,{useEffect,useRef,useState} from 'react';
import {phases,storyAt,boundedTime,advanceStory,missionProofView,exampleCheckView,exampleDialogKey} from './mission.mjs';
import './mission.css';
import {Signal} from './replay.jsx';
import {advanceReplay,storyRecordingOffset} from './replay.mjs';

export function Mission({api,onWorkspace,playback,onPlayback}) {
  const {seconds,playing}=playback,story=storyAt(seconds),beat=phases[story.phase];
  const [retry,setRetry]=useState(0),[proofRetry,setProofRetry]=useState(0),[proof,setProof]=useState(null),[proofError,setProofError]=useState('');
  const [examplesOpen,setExamplesOpen]=useState(false);
  const exampleTrigger=useRef(null);
  const openExamples=()=>{setSourcePlaying(false);onPlayback(current=>({...current,playing:false,sourcePlaying:false}));setExamplesOpen(true);};
  const reduced=useRef(typeof matchMedia==='function'&&matchMedia('(prefers-reduced-motion: reduce)').matches).current;
  const clock=useRef(playback.recordingSeconds||0),[recording,setRecording]=useState(clock.current);
  const [sourcePlaying,setSourcePlaying]=useState(playback.sourcePlaying??(!reduced&&seconds===0));
  const [limits,setLimits]=useState({});const ready=Object.keys(limits).length===2&&Object.values(limits).every(Number.isFinite);
  const limit=ready?Math.max(...Object.values(limits)):50;
  const latest=useRef(onPlayback);latest.current=onPlayback;
  const onReady=React.useCallback((name,value)=>setLimits(current=>({...current,[name]:value})),[]);
  useEffect(()=>{let active=true;const controller=new AbortController();setProof(null);setProofError('');api('/api/mission-proof',{signal:controller.signal}).then(value=>{if(active){if(!missionProofView(value,0)||!missionProofView(value,30))throw Error('Incomplete workflow evidence');setProof(value);}}).catch(e=>{if(active&&e.name!=='AbortError')setProofError(e.message);});return()=>{active=false;controller.abort();};},[api,proofRetry]);
  useEffect(()=>{let id,previous=null,lastUi=0,elapsedSinceUi=0,previousStory=null;
  const resetWallClock=()=>{previous=null;previousStory=null;elapsedSinceUi=0;};
  document.addEventListener('visibilitychange',resetWallClock);
  // Canvas reads this source clock each frame; React receives only ten updates/second.
  id=requestAnimationFrame(function run(now){
    if(previousStory!==null&&!document.hidden)elapsedSinceUi+=(now-previousStory)/1000;previousStory=document.hidden?null:now;
    const elapsed=previous===null||document.hidden?0:(now-previous)/1000;previous=document.hidden?null:now;
    clock.current=advanceReplay(clock.current,elapsed,ready&&sourcePlaying,limit);
    if(now-lastUi>=100){lastUi=now;setRecording(clock.current);const storyElapsed=elapsedSinceUi;elapsedSinceUi=0;latest.current(current=>{const next=advanceStory(current.seconds,storyElapsed,current.playing&&!document.hidden);return {...current,seconds:next,playing:current.playing&&next<30,recordingSeconds:clock.current,sourcePlaying:sourcePlaying&&clock.current<limit};});}
    if(clock.current>=limit&&sourcePlaying)setSourcePlaying(false);
    id=requestAnimationFrame(run);
  });return()=>{cancelAnimationFrame(id);document.removeEventListener('visibilitychange',resetWallClock);};},[sourcePlaying,ready,limit]);
  useEffect(()=>{if(seconds>=30)setSourcePlaying(false);},[seconds]);
  const seek=value=>{const next=boundedTime(value);clock.current=storyRecordingOffset(next,limit);setRecording(clock.current);setSourcePlaying(false);onPlayback(current=>({...current,seconds:next,playing:false,recordingSeconds:clock.current,sourcePlaying:false}));};
  const phaseKey=e=>{const index=Array.from(e.currentTarget.children).indexOf(e.target);if(index<0)return;const next=e.key==='ArrowRight'?(index+1)%4:e.key==='ArrowLeft'?(index+3)%4:e.key==='Home'?0:e.key==='End'?3:null;if(next===null)return;e.preventDefault();seek(phases[next].start);e.currentTarget.children[next].focus();};
  const replay=()=>{clock.current=0;setRecording(0);setSourcePlaying(true);onPlayback(current=>({...current,seconds:0,playing:true,recordingSeconds:0,sourcePlaying:true}));};
  const play=()=>{if(seconds>=30||seconds===0&&!playing){replay();return;}setSourcePlaying(!playing);onPlayback(current=>({...current,playing:!playing,sourcePlaying:!playing}));};
  return <div className="mission-shell">
    <header className="mission-top"><a className="mission-brand" href="#main" onClick={e=>{e.preventDefault();document.getElementById('main')?.focus();}}>BioTwin <span>SYNC</span></a><span className="mission-top-label">ONBOARD EVIDENCE · HUMAN GUIDANCE</span><button ref={exampleTrigger} className="mission-examples-trigger" onClick={openExamples}>View example checks</button><button onClick={()=>onWorkspace({phase:story.phase,replied:story.replied,tab:'monitor'})}>Open workspace <span aria-hidden="true">↗</span></button></header>
    {examplesOpen&&<EvidenceChecks proof={proof} onClose={()=>setExamplesOpen(false)} trigger={exampleTrigger}/>}
    <main id="main" className="mission-main" tabIndex="-1">
      <div className="mission-intro"><div><h1>Recheck delayed instructions <span>onboard.</span></h1><p className="mission-subtitle">Help crew keep measurements and clinician instructions usable while Earth replies are delayed.</p></div><div className="mission-playback"><button className="mission-primary" onClick={play}>{playing?'Pause example':seconds===0?'Watch 30-second example':seconds>=30?'Watch again':'Resume example'} <span aria-hidden="true">{playing?'Ⅱ':'▶'}</span></button><div><span>{String(Math.floor(seconds)).padStart(2,'0')} / 30 s</span><button onClick={replay}>Restart example</button></div></div></div>
      <nav className="mission-phases" aria-label="Example phases" onKeyDown={phaseKey}>{phases.map((phase,i)=><button key={phase.start} aria-current={story.phase===i?'step':undefined} onClick={()=>seek(phase.start)}><span>0{i+1} · {phase.time} s</span><strong>{phase.label}</strong></button>)}</nav>
      <label className="mission-scrub">Example position <input type="range" min="0" max="30" step="0.1" value={seconds} onChange={e=>seek(Number(e.target.value))} aria-valuetext={`${seconds.toFixed(1)} seconds, ${beat.label}`}/><span>{story.complete?'Example complete':playing?'Playing':'Paused'}</span></label>
      <section className="mission-delay" aria-label="Simulated spacecraft Earth communication"><div className="mission-route-labels"><strong>CREW / ONBOARD<small>Local support stays available</small></strong><span>20 min each way · simulated</span><strong>CLINICIAN / EARTH<small>Reviews delivered evidence</small></strong></div><div className="mission-route" aria-hidden="true"><span className="mission-vessel">◇</span><div className="mission-lanes"><div><span>Evidence →</span><i style={{left:`${story.outbound*100}%`}} className={seconds>=6?'visible':''}/></div><div><span>← Human-written reply</span><i style={{left:`${(1-story.inbound)*100}%`}} className={story.received?'visible':''}/></div></div><span className="mission-earth">◉</span></div><div className="mission-route-caption"><span>{story.received?'Earth has the earlier check':'Evidence reaches Earth at +20 min'}</span><span>{story.replied?'Reply arrived at +40 min':'Reply reaches crew at +40 min'}</span></div></section>
      <div className="mission-canvas"><section className="mission-signals" aria-label="Original NASA recordings"><div className="mission-section-heading"><h2>Real NASA recordings</h2><div className="mission-record-controls"><span>{recording.toFixed(1)} s · 1×</span><button disabled={!ready} onClick={()=>{if(recording>=limit)clock.current=0;setSourcePlaying(v=>!v);}}>{sourcePlaying?'Pause recordings':recording>=limit?'Replay recordings':'Play recordings'}</button></div></div><p className="mission-signal-note">ECG = heart electrical signal. Pulse = optical pulse recording. Separate source samples, independent of the response-speed example.</p><Signal api={api} example="crew001_ecg.json" name="ECG" clock={clock} reduced={reduced} retry={retry} onReady={onReady} onRetry={()=>setRetry(v=>v+1)}/><Signal api={api} example="crew001_bvp.json" name="Pulse" clock={clock} reduced={reduced} retry={retry} onReady={onReady} onRetry={()=>setRetry(v=>v+1)}/></section>
      <aside className="mission-response"><div className="mission-beat-copy" aria-live="polite"><p className="mission-script-label">Scripted scenario · {beat.status.toLowerCase()}</p><h2>{beat.title}</h2></div><MissionProof proof={proof} seconds={seconds} error={proofError} onRetry={()=>setProofRetry(v=>v+1)}/><button className="mission-review-button" onClick={()=>onWorkspace({phase:story.phase,replied:story.replied,tab:story.replied?'selfcheck':'monitor'})}>{story.replied?'Inspect this checklist in workspace ↗':'Try the workflow in workspace ↗'}</button></aside></div>
      <div className="mission-attribution"><span>NASA SOTERIA · original ground flight-simulation ECG / BVP samples · CC BY 4.0</span><a href="https://doi.org/10.1038/s41597-025-05336-7" target="_blank" rel="noreferrer">Fettrow et al., 2025 ↗</a></div>
      <details className="mission-details"><summary>Sources, implemented technology & limits</summary><div><p>Current scripted scenario: {beat.detail}</p><p>The displayed traces are bundled NASA SOTERIA excerpts from another source participant, independent of the scripted crew and your recorded sessions. ECG records the heart’s electrical activity; BVP (blood volume pulse) records an optical pulse signal. The samples have separate acquisition windows. Synchronization and amplitude units are unverified. Original acquisition seconds, fixed amplitude scales, missing samples and timestamp gaps are preserved. No live acquisition, clinical diagnosis or physiological inference is performed.</p><p>The 30-second example compresses a fixed simulated 20-minute one-way delay. Earth receives evidence at +20 minutes; a newer onboard check is recorded at +30; the reply reaches the crew at +40. Physician processing time is omitted. The crew report and consultation narrative are scripted demonstration content, not a real consultation or medical prescription.</p><p>The reaction-time example is synthetic response speed measured in milliseconds, independent of the NASA traces. The isolated workflow uses the actual offline protocol evaluator to re-evaluate human-authored conditional instructions on arrival. AI does not author these instructions. Clinical AI and prescribing are not implemented. Experimental ECG models that failed their gates remain inactive. This prototype does not provide automatic medical diagnosis. No clinical validation or NASA endorsement is claimed.</p><p>NASA NLSP UTMB Campaign 3 cognitive/research data structures informed the separate reference workflow. These are separate from SOTERIA; raw NLSP data is excluded from this bundle pending redistribution clarification. Open workspace to inspect existing recorded evidence and the crew/clinician workflow. Playing this example does not write to that session.</p></div></details>
    </main>
  </div>;
}

export function MissionProof({proof,seconds,error,onRetry}) {
  const view=missionProofView(proof,seconds);
  const value=(number,unit='ms')=>Number.isFinite(number)?`${number} ${unit}`:'Not received';
  if(!view)return <section className="mission-proof" aria-label="Workflow computation"><h3>Local workflow computation</h3><p role="status">{error?'Workflow proof unavailable. The scripted example can still play; no computed checklist is shown.':'Loading the isolated workflow computation...'}</p>{error&&<button onClick={onRetry}>Retry computation</button>}</section>;
  const {phase,steps,gates,dependencies,evaluated,arrived,outcome}=view;
  return <section className="mission-proof" aria-label="Computed synthetic workflow"><h3>Actual local rule engine · synthetic inputs</h3><p className="mission-metric-definition">Reaction time = response speed, in milliseconds (ms).</p><div className="mission-payoff-card"><div><span>Earth reviewed</span><strong>{value(phase.doctor.metric.value)}</strong></div><span className="mission-payoff-arrow" aria-hidden="true">→</span><div><span>Onboard now</span><strong>{value(phase.crew.metric.value)}</strong></div></div><div className={'mission-outcome '+(evaluated?'selected':arrived?'held':'')}><strong>{outcome}</strong><p>{evaluated?steps[0]:arrived?'Review the missing or held evidence before using an instruction.':'Human-written conditions will be checked against onboard evidence when the reply arrives.'}</p></div>{arrived&&<div className="mission-gates" aria-label="Returned evidence checks">{gates.length?gates.map(gate=><span key={gate.id} className={'mission-gate '+gate.state} title={gate.measurement}>{gate.state==='pass'?'✓ ':gate.state==='missing'?'Missing: ':'Held: '}{gate.label}</span>):<span className="mission-gate missing">Evidence checks not returned</span>}</div>}<p className="mission-clinical-limit">Workflow demonstration, not automatic medical diagnosis.</p><details><summary>Computed evidence & checklist</summary><p>Mission +{phase.mission_seconds/60} min. {evaluated?`The evaluator selected human-authored branch ${phase.crew.checklist.branch}.`:phase.crew.checklist.branch?`Returned branch ${phase.crew.checklist.branch}; evidence checks are missing or held.`:'No current checklist branch has been selected.'}</p>{arrived&&steps.length>0&&!evaluated&&<p>Returned checklist steps are retained below; the instruction remains held for evidence review.</p>}{arrived&&steps.length>0&&<ol>{steps.map((step,i)=><li key={i}>{step}</li>)}</ol>}{phase.crew.checklist.reasons?.length>0&&<p>{phase.crew.checklist.reasons.join('; ')}</p>}{dependencies.map((dep,i)=><p key={i}>{String(dep.metric||'Required measurement').replaceAll('_',' ')}: reviewed {value(dep.reviewed?.value,dep.reviewed?.unit||dep.unit||'')}, current {value(dep.current?.value,dep.current?.unit||dep.unit||'')}. Current source: {dep.current?.source_id||'missing'}; method: {dep.current?.method||'missing'}; evaluator reason: {dep.current?.reason|| (dep.current?.usable===true?'usable':'not returned')}.</p>)}<p className="mission-proof-digest">Integrity digests check snapshot consistency; they do not authenticate a clinician.<br/>Current snapshot: {phase.crew.digest||'not returned'}<br/>Reviewed snapshot: {phase.crew.reviewed?.digest||'not received'}</p></details></section>;
}


export function EvidenceChecks({proof,onClose,trigger}) {
  const [selected,setSelected]=useState('below'),dialog=useRef(null);
  useEffect(()=>{
    const element=dialog.current,previousOverflow=document.body.style.overflow;
    element.showModal();document.body.style.overflow='hidden';
    element.querySelector('button')?.focus();
    return()=>{element.close();document.body.style.overflow=previousOverflow;trigger?.current?.focus();};
  },[]);
  const keydown=e=>{
    const buttons=Array.from(dialog.current.querySelectorAll('button:not(:disabled),a[href],input,summary,[tabindex="0"]'));
    const action=exampleDialogKey(e.key,e.shiftKey,buttons.indexOf(document.activeElement),buttons.length);
    if(action===null)return;e.preventDefault();if(action==='close')onClose();else buttons[action]?.focus();
  };
  const view=exampleCheckView(proof,selected);
  const symbols={lt:'<',le:'\u2264',eq:'=',ge:'\u2265',gt:'>'};
  return <dialog ref={dialog} className="mission-example-dialog" aria-labelledby="example-check-title" onKeyDown={keydown} onCancel={e=>{e.preventDefault();onClose();}}>
    <div className="mission-example-heading"><div><p>Synthetic examples · illustrative threshold</p><h2 id="example-check-title">Try the evidence check</h2></div><button onClick={onClose} aria-label="Close example checks">Close</button></div>
    <p className="mission-example-intro">One human-authored rule and its checklists, checked against three isolated inputs. These examples show computed behavior; they do not demonstrate clinical benefit.</p>
    <div className="mission-example-options" role="group" aria-label="Choose synthetic example">{[['below','420 ms · below threshold'],['above','585 ms · above threshold'],['stale','585 ms · too old']].map(([id,label])=><button key={id} aria-pressed={selected===id} onClick={()=>setSelected(id)}>{label}</button>)}</div>
    {!view?<p role="status">Example evidence unavailable. No A/B instruction is selected.</p>:<ExampleCheckResult view={view} symbols={symbols}/>}
    <p className="mission-example-limit">Independent of NASA recordings and your active workspace. A and B name authored branches, not safety ratings. No clinical validation is claimed.</p>
  </dialog>;
}

export function ExampleCheckResult({view,symbols={lt:'<',le:'\u2264',eq:'=',ge:'\u2265',gt:'>'}}) {
  const {item,dep,protocol,rules,evaluation,selected,steps}=view;
  const ageLabel=seconds=>`${Math.floor(seconds/60)} min${seconds%60?` ${seconds%60} s`:''}`;
  return <div className="mission-example-result" aria-live="polite">
    <div className="mission-example-rule"><strong>Same authored condition in every example</strong>{['A','B'].map(branch=><p key={branch}>{branch}: {rules[branch].map(rule=>`${rule.metric.replaceAll('_',' ')} ${symbols[rule.op]} ${rule.value.toFixed(2)} ${dep.unit||''}`).join(' AND ')}</p>)}<small>Displayed cutoff rounded; exact rule below. This is an illustrative rule, not a clinical limit.</small><details><summary>Exact stored condition &amp; timing</summary>{['A','B'].map(branch=><p key={branch}>{branch}: {rules[branch].map(rule=>`${rule.metric.replaceAll('_',' ')} ${symbols[rule.op]} ${rule.value} ${dep.unit||''}`).join(' AND ')}</p>)}<p>Measurement age: {item.measurement_age_seconds} s / allowed {protocol.freshness_seconds} s.</p></details></div>
    <dl className="mission-example-facts"><div><dt>Earth reviewed</dt><dd>{dep.reviewed.value} {dep.reviewed.unit}</dd></div><div><dt>Current measurement</dt><dd>{dep.current.value} {dep.current.unit}</dd></div><div><dt>Measurement age at check</dt><dd>{ageLabel(item.measurement_age_seconds)} / limit {ageLabel(protocol.freshness_seconds)}</dd></div><div><dt>Source / method</dt><dd>{dep.current.source_id}<br/>{dep.current.method}</dd></div></dl>
    <p className="mission-example-gate">Source / method: {dep.current.compatible===true?'match':'held'}. Freshness / quality: {dep.current.usable===true&&!dep.current.reason?'usable':dep.current.reason||'held'}.</p>
    <div className={'mission-outcome '+(selected?'selected':'held')}><strong>{selected?`Evaluator selected branch ${evaluation.outcome}`:`Instruction held · returned ${evaluation.outcome}`}</strong><p>{selected?'Current usable evidence satisfies this branch of the same authored condition.':'No active A/B instructions. Repeat the supported measurement with its method and time, and request reviewer review.'}</p></div>
    <p className="mission-example-reason">Evaluator reason: {evaluation.reasons?.join('; ')||'not returned'}</p>
    {selected?<><h3>Actual authored checklist · {evaluation.outcome}</h3><ol>{steps.map((step,i)=><li key={i}>{step}</li>)}</ol></>:evaluation.outcome==='UNKNOWN'&&<><h3>Actual authored evidence-recovery checklist · UNKNOWN</h3><ol>{evaluation.checklist.map((step,i)=><li key={i}>{step}</li>)}</ol></>}
  </div>;
}
