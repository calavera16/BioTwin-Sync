import {monitorReading,preferredMetric,workflowCue,instantMicros} from './monitor.mjs';
import {cleanSourceLabel,contextOf} from './model.mjs';

export const journeySteps=Object.freeze([
  {label:'Record a reading',tab:'monitor'},
  {label:'Send to Earth',tab:'consultation'},
  {label:'Review reply',tab:'selfcheck'},
  {label:'Record what was done',tab:'selfcheck'},
]);
export function noviceWorkflow(state,selected){
  const reading=monitorReading(state,preferredMetric(state,selected));
  const c=state.projection.checklist, messages=state.messages||[];
  const hasReading=Number.isFinite(reading.value),snapshotAt=instantMicros(state.projection.snapshot_at||reading.observed_at);
  const sentCurrent=messages.some(m=>{if(m.sender!=='crew'||m.kind!=='snapshot')return false;const sentAt=instantMicros(m.sent_at);return snapshotAt!==null&&sentAt!==null&&sentAt>=snapshotAt;});
  const received=!!state.projection.protocol_evidence||(c.steps||[]).length>0;
  // A milestone records that a consultation happened; routing separately asks
  // whether the current snapshot has been sent. A reply/follow-up proves the
  // earlier consultation even when its original message is not in this view.
  const sent=received||messages.some(m=>m.sender==='crew'&&['snapshot','followup'].includes(m.kind));
  const completed=(c.current_completion_count??c.completion_count??0)>0;
  const progress=[hasReading,sent,received,completed];
  const active=received?3:sent?2:hasReading?1:0;
  let cue=state.mode==='guided'?{kind:'preview',label:state.role==='doctor'?'Review delivered example':'Try this workflow interactively',detail:'Read-only synthetic example. Scene buttons show saved moments; they do not record your actions.',...(state.role==='doctor'?{tab:'consultation'}:{action:'start_interactive',payload:{}})}:workflowCue(state,selected);
  const pending=(state.transport?.packets||[]).filter(p=>p.status==='in transit');
  const currentDigest=contextOf(state).digest;
  const replyMatchesCurrent=!!currentDigest&&state.projection.protocol_evidence?.reviewed_digest===currentDigest;
  if(state.mode!=='guided'&&!['urgent','stale','steps','missing'].includes(cue.kind)){
    if(pending.length&&state.actions.advance)cue={kind:'waiting',label:'Advance delivery +20 min',detail:'This prototype has a simulated clock. Advance it explicitly to deliver packets that are due; it does not speed up a real radio link.',action:'advance',payload:{}};
    else if(state.role==='crew'&&sentCurrent&&!received&&!pending.length)cue={kind:'review',label:'Review at Earth',detail:'The consultation reached Earth. Open the flight surgeon terminal to review the delivered reading and write instructions.',role:'doctor'};
    else if(state.role==='doctor'&&replyMatchesCurrent&&messages.some(m=>m.sender==='doctor'&&m.kind==='protocol')&&!pending.length)cue={kind:'review',label:'Review reply onboard',detail:'The reply has arrived. Open the crew terminal to read the instructions evaluated against onboard evidence.',role:'crew'};
  }
  return {progress,active,cue,mode:state.mode==='guided'?'Read-only guided example':state.mode==='operator'?'Interactive practice / synthetic readings':'Your recorded inputs / source and method retained'};
}
export const metricMeaning=id=>({reaction_time:'Reaction time is response speed, measured in milliseconds (ms). A larger number means a slower response.',heart_rate:'Heart rate is the number of heartbeats per minute (bpm).',spo2:'SpO2 is the oxygen-saturation percentage reported by a device.',sleep_duration:'Sleep duration is hours recorded in a diary; this does not measure sleep stages.',sleep_quality:'Sleep quality is a self-reported score out of 10.'}[id]||'This view shows recorded values with their original measurement unit.');
export function chronologicalRows(rows=[]){
  return rows.map((row,index)=>({row,index,t:instantMicros(row.observed_at)})).sort((a,b)=>a.t===b.t?a.index-b.index:a.t===null?1:b.t===null?-1:a.t<b.t?-1:1).map(x=>x.row);
}
export function manualDefaults(state,metric){
  if(state.mode!=='local'||!state.local?.observations)return {source:'',method:''};
  const r=monitorReading(state,metric);
  return r.source_id&&r.method&&r.observed_at?{source:cleanSourceLabel(r.source_id),method:r.method}:{source:'',method:''};
}
export function localDateTime(value=Date.now()){
  const date=new Date(value);return Number.isFinite(date.valueOf())?new Date(date.valueOf()-date.getTimezoneOffset()*60000).toISOString().slice(0,19):'';
}
export function validateObservationTime(value,now=Date.now()){
  const at=Date.parse(value);
  if(!Number.isFinite(at))return 'Enter the date and time when the device reading was taken.';
  if(at>now)return `Reading time must be at or before ${localDateTime(now).replace('T',' ')} (your local computer time). The simulated mission clock cannot authorize a future reading. Choose the actual earlier acquisition time.`;
  return '';
}
export function sessionSaveStatus(state,baseline){
  if(!baseline||baseline.session_id!==state.session_id)return {kind:'unknown',label:'No export requested this visit'};
  if(baseline.kind==='new')return {kind:'new',label:'New recorded session / export needed'};
  if(baseline.revision!==state.revision)return {kind:'changed',label:baseline.kind==='opened'?'Changed since saved file / export needed':'Changed since export request / export needed'};
  return baseline.kind==='opened'?{kind:'opened',label:'Opened saved file / unchanged'}:{kind:'requested',label:'Export requested / verify saved file'};
}
export function recordingLabel(signal,index=0){
  return `${signal.channel||'Signal'} / ${cleanSourceLabel(signal.source_id)} / ${signal.provenance==='synthetic'?'synthetic demo':signal.provenance==='recorded'?'recorded source (unverified)':'provenance not supplied'} / ${signal.sample_count||0} samples / #${index+1}`;
}

// Shared by the compact status and expanded comparison: an absent outcome
// is different from a failed dependency, even when all technical gates pass.
export function protocolOutcome(state){
  const dependencies=state.projection.protocol_evidence?.dependencies||[],c=state.projection.checklist;
  if(!dependencies.length)return {kind:'missing'};
  if(c.branch==='UNKNOWN'||dependencies.some(d=>d.current?.usable!==true))return {kind:'held'};
  if(['A','B'].includes(c.branch)&&c.steps?.length>0)return {kind:'evaluated',branch:c.branch};
  return {kind:'waiting'};
}
export function technologyStatus(state){
  const evidence=state.projection.protocol_evidence,c=state.projection.checklist;
  const dependencies=evidence?.dependencies||[];
  if(!dependencies.length){
    const pending=(state.transport?.packets||[]).filter(p=>p.status==='in transit').length;
    return {kind:pending?'waiting':'missing',label:pending?`Delivery waiting: ${pending} packet(s) in transit. No rule evidence in this terminal yet.`:state.actions.author_reply?'Delivered evidence is ready for human-written instructions. No rule evidence yet.':'No rule evidence received yet. Record and send a reading to begin.'};
  }
  const value=r=>Number.isFinite(r?.value)?`${r.value} ${r.unit||''}`.trim():'not recorded';
  const readings=dependencies.map(d=>`${d.metric.replaceAll('_',' ')}: reviewed ${value(d.reviewed)} / ${state.role==='doctor'?'delivered':'onboard'} ${value(d.current)}`).join('; ');
  const outcome=protocolOutcome(state);
  const result=outcome.kind==='evaluated'?`Backend selected instruction outcome ${outcome.branch}.`:outcome.kind==='held'?c.branch==='UNKNOWN'?'Backend reported UNKNOWN outcome; no instruction selected.':'Rule held: usable matching evidence is not established.':state.role==='doctor'?'Awaiting onboard evaluation. Review delivered evidence; no instruction outcome is reported here.':'No applicable instruction outcome reported. Review received evidence.';
  return {kind:outcome.kind,label:`${readings}. ${result}`};
}
