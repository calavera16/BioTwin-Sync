// These selectors accept one terminal's API projection. They never combine roles.
export const monitorMetrics=[
  {id:'reaction_time',label:'Cognition',name:'Reaction time',unit:'ms',sensor:null},
  {id:'heart_rate',label:'Cardiovascular',name:'Heart rate',unit:'bpm',sensor:'HR'},
  {id:'spo2',label:'Oxygen',name:'SpO₂',unit:'%',sensor:'SpO2'},
  {id:'temperature',label:'Temperature',name:'Temperature',unit:'°C',sensor:'temperature'},
  {id:'sleep_duration',label:'Sleep',name:'Sleep duration',unit:'h',sensor:'sleep'},
];
export const extraMetrics=[{id:'systolic_bp',name:'Systolic BP',unit:'mmHg',sensor:'BP'},{id:'diastolic_bp',name:'Diastolic BP',unit:'mmHg'},{id:'sleep_quality',name:'Sleep quality',unit:'/10'}];
const contexts=['subject_id','source_id','metric','unit','phase','method'];
const same=(a,b)=>contexts.every(k=>a[k]===b[k]);
const usable=r=>r.quality==='usable'&&Number.isFinite(r.value);
export function instantMicros(value){
  const milliseconds=Date.parse(value);
  if(!Number.isFinite(milliseconds))return null;
  // Validated ISO timestamps retain Python's microsecond precision for latest
  // selection. Date.parse normalizes timezone offsets but truncates fractions
  // after milliseconds; add only that lost remainder, without floating error.
  const fraction=String(value).match(/[Tt ]\d{2}:\d{2}:\d{2}\.(\d+)/)?.[1]||'';
  return BigInt(milliseconds)*1000n+BigInt(fraction.padEnd(6,'0').slice(3,6));
}
export function monitorReading(state,id){
  const spec=[...monitorMetrics,...extraMetrics].find(m=>m.id===id);
  if(!spec)throw Error('Unknown monitor metric');
  if(id==='reaction_time'){
    const m=state.projection.metric;
    return {...spec,...m,name:spec.name,points:(m.history||[]).map(r=>({t:Date.parse(r.observed_at),value:usable(r)?r.value:null})),reason:m.quality==='usable'?'':m.quality==='review'?'Needs review':'No reading'};
  }
  const rows=(state.local?.observations||[]).filter(r=>r.metric===id).map(row=>({row,instant:instantMicros(row.observed_at)})).filter(r=>r.instant!==null);
  if(rows.length){
    const latest=rows.reduce((latest,r)=>r.instant>latest?r.instant:latest,rows[0].instant),current=rows.filter(r=>r.instant===latest);
    if(current.length!==1)return {...spec,value:null,quality:'missing',points:[],reason:'Conflicting latest readings'};
    const row=current[0].row;
    // Keep review-quality values visible, but never chart them as usable samples.
    const comparable=rows.filter(r=>same(r.row,row)).sort((a,b)=>a.instant<b.instant?-1:a.instant>b.instant?1:0);
    return {...spec,...row,points:comparable.map(({row:r})=>({t:Date.parse(r.observed_at),value:usable(r)?r.value:null})),reason:row.quality==='review'?'Needs review':row.quality==='missing'?'No reading':''};
  }
  const sensor=spec.sensor&&state.projection.sensors?.[spec.sensor];
  // BP projection can be a paired string: do not put it on a numeric axis.
  const value=Number.isFinite(sensor?.value)?sensor.value:null;
  return {...spec,...sensor,value,points:[],reason:sensor?.reason||'No reading'};
}
export function captureTarget(state,metric='reaction_time'){
  if(state.role==='doctor')return null;
  if(state.mode==='local')return metric==='reaction_time'?{action:'capture_reaction',payload:{}}:{action:'capture_manual',payload:{metric}};
  return state.mode==='operator'?{action:'capture',payload:{value:''}}:{action:'start_local',payload:{}};
}
export function activeIntake(state){
  const assistant=state.local?.assistant,records=assistant?.handoff?.records||assistant?.records||[];
  return records.filter(r=>r.kind==='assessment').reduce((latest,r)=>{
    if(!latest)return r;const a=instantMicros(r.context?.origin_created_at),b=instantMicros(latest.context?.origin_created_at);
    return a!==null&&(b===null||a>b||a===b&&String(r.id)>String(latest.id))?r:latest;
  },null);
}
export function preferredMetric(state,current='reaction_time'){
  if(!state)return current||'reaction_time';
  const all=[...monitorMetrics,...extraMetrics];
  const validCurrent=all.some(m=>m.id===current)?current:'reaction_time';
  const curReading=monitorReading(state,validCurrent);
  if(Number.isFinite(curReading.value)||curReading.quality==='review')return validCurrent;
  const depMetric=state.projection?.protocol_evidence?.dependencies?.find(d=>all.some(m=>m.id===d.metric))?.metric;
  if(depMetric)return depMetric;
  const obs=state.local?.observations||[];
  for(let i=obs.length-1;i>=0;i--){
    const m=obs[i]?.metric;
    if(all.some(spec=>spec.id===m))return m;
  }
  const available=all.find(m=>Number.isFinite(monitorReading(state,m.id).value));
  return available?available.id:validCurrent;
}
export function workflowCue(state,selected='reaction_time'){
  const metric=preferredMetric(state,selected);
  const p=state.projection,c=p.checklist,assistant=state.local?.assistant,handoff=assistant?.handoff,latest=activeIntake(state);
  if(handoff?.urgent_history?.length)return {kind:'urgent',label:'Review reported urgent history',detail:'Reported history remains available; it is not a current physiological assessment.',tab:'assistant'};
  if(state.role==='doctor')return {kind:'review',label:state.actions.author_reply?'Write conditional checklist':'Review delivered consultation',detail:'Earth sees delivered snapshots only.',...(state.actions.author_reply?{action:'author_reply',payload:{metric,operator:'ge',threshold:'',original_reply:'',checklist_a:'',checklist_b:''}}:{tab:'consultation'})};
  const historical=c.followup_context?.basis==='historical';
  if(latest?.stale)return {kind:'stale',label:'Reassess current intake',detail:'The latest intake is stale. Record a fresh reassessment or contact onboard help.',tab:'assistant'};
  if(c.branch==='UNKNOWN')return {kind:'missing',label:'Review missing checklist evidence',detail:'The received condition cannot be evaluated. Review its actual dependencies and repeat the missing or unusable measurement.',tab:'selfcheck'};
  if(historical)return {kind:'stale',label:state.actions.followup?'Review historical follow-up':'Review changed evidence',detail:c.followup_context?.message||'Earlier completions belong to changed evidence. Review the current steps.',tab:'selfcheck'};
  const count=c.current_completion_count??c.completion_count??0;
  if(c.steps.length&&count<c.steps.length)return {kind:'steps',label:'Continue received steps',detail:`${count} / ${c.steps.length} recorded · ${c.steps.find((_,i)=>!(c.completed_steps||[]).includes(i))||'Review the received instruction.'}`,tab:'selfcheck'};
  const followupPending=(state.transport?.packets||[]).some(p=>p.status==='in transit'&&(state.messages||[]).some(m=>m.id===p.event_id&&m.kind==='followup'));
  const completionTimes=(c.completions||[]).filter(r=>!r.stale_context).map(r=>Date.parse(r.completed_at)).filter(Number.isFinite),lastCompletion=completionTimes.length?Math.max(...completionTimes):null;
  const sentCurrent=lastCompletion!==null&&(state.messages||[]).some(m=>m.kind==='followup'&&m.sender==='crew'&&Date.parse(m.sent_at)>=lastCompletion);
  if(c.steps.length&&state.actions.followup&&!sentCurrent&&!followupPending)return {kind:'complete',label:'Send completed follow-up',detail:c.followup_context?.message||'Current applicable steps are recorded. Send their evidence to Earth.',action:'followup',payload:{}};
  const pending=(state.transport?.packets||[]).filter(p=>p.status==='in transit');
  if(pending.length)return {kind:'waiting',label:'Review delivery in progress',detail:`${pending.length} packet${pending.length===1?'':'s'} in transit. New evidence stays local until delivered.`,tab:'consultation'};
  if(sentCurrent)return {kind:'complete',label:'Review sent follow-up',detail:'Current completion evidence has been sent. Review its delivery in the communication record.',tab:'consultation'};
  const reading=monitorReading(state,metric);
  if(Number.isFinite(reading.value)&&reading.quality==='usable'&&state.actions.send_consultation)return {kind:'ready',label:'Send consultation',detail:'Send the current onboard evidence for delayed Earth review.',action:'send_consultation',payload:{}};
  return {kind:reading.quality==='review'?'review':'missing',label:state.mode==='local'?`Record ${reading.name.toLowerCase()}`:state.mode==='operator'?'Capture synthetic reaction':'Start recorded session',detail:reading.quality==='review'?'Latest reading needs review; it is excluded from the usable trace.':c.steps.length?'Applicable steps are recorded. Capture new evidence when needed.':'Collect source-linked evidence to begin.',...captureTarget(state,metric)};
}
export function evidenceEvents(state){
  const now=Date.parse(state.at),events=[];
  for(const p of state.transport?.packets||[]){if(Date.parse(p.at)<=now)events.push({t:Date.parse(p.at),label:`${p.sender==='doctor'?'Earth':'Crew'} packet sent`});if(['arrived','delivered'].includes(p.status)&&Date.parse(p.arrives_at)<=now)events.push({t:Date.parse(p.arrives_at),label:`${p.sender==='doctor'?'Crew':'Earth'} received`});}
  return events.sort((a,b)=>a.t-b.t).slice(-6);
}
export function packetProgress(packet,at){
  const sent=Date.parse(packet.at),arrival=Date.parse(packet.arrives_at),now=Date.parse(at);
  if(!Number.isFinite(sent)||!Number.isFinite(arrival)||!Number.isFinite(now)||arrival<=sent)return {progress:null,seconds:null};
  return {progress:Math.max(0,Math.min(1,(now-sent)/(arrival-sent))),seconds:Math.max(0,Math.ceil((arrival-now)/1000))};
}
