export const roleName=role=>role==='doctor'?'Flight surgeon':'Crew';
export const time=value=>{
  if(!value)return 'Unavailable';
  const date=new Date(value);
  return Number.isNaN(date.valueOf())?'Unavailable':date.toISOString().slice(11,16)+' UTC';
};
export const fullTime=value=>{
  if(!value)return 'Unavailable';
  const date=new Date(value);
  return Number.isNaN(date.valueOf())?'Unavailable':date.toISOString().replace('T',' ').replace('.000Z',' UTC');
};
export function connectionMessage(error){
  return error.status===401||error.status===403
    ? 'This local session is unavailable or has expired. Reopen BioTwin Sync to reconnect.'
    : 'Cannot reach the local app service. Keep BioTwin Sync open, then retry. '+error.message;
}
export function actionReason(state,action,busy=false){
  if(busy)return 'Another action is being submitted. Please wait.';
  if(state.actions[action])return '';
  const reasons={capture:'Start interactive mode to capture a synthetic measurement.',send_consultation:'Open Monitor and record a usable reading, then send its evidence to Earth.',author_reply:'A consultation must arrive at Earth before you can author instructions. Open Consultation to advance delivery, then review the received evidence.',complete:'An applicable received instruction is required. Open Consultation to review delivery, or repeat a held measurement in Monitor.',followup:'Complete the applicable checklist with explicit notes before sending follow-up.',advance:'No delivery advance is available in this scene.'};
  return reasons[action]||'This action is unavailable for the current evidence.';
}
export const valueText=value=>value===null||value===undefined?'Unavailable':String(value);
export const cleanSourceLabel=value=>typeof value==='string'&&value.trim()?value.replace(/^LOCAL:[^:]+:/,''):'No source';
export const contextOf=s=>({role:s.role,crew:s.crew,session_id:s.session_id,revision:s.revision,digest:Object.hasOwn(s,'snapshot_digest')?s.snapshot_digest:s.local?.snapshot_digest??s.projection.digest});
export const sameContext=(a,b)=>!!a&&!!b&&['role','crew','session_id','revision','digest'].every(k=>a[k]===b[k]);
export const importByteLimit=action=>(action==='import_session'?8:2)*1024*1024;
export const assistantToolTarget=tool=>({manual:{tab:'measurements',action:'capture_manual'},reaction:{tab:'measurements',action:'capture_reaction'},signal:{tab:'recordings',action:null}}[tool]||null);
export const signalWindowVisible=(windowData,signal,context)=>!!signal&&windowData?.signal_id===signal&&sameContext(windowData.context,context);
export const canSubmitContext=(context,current,epoch)=>!!current&&(context.epoch===undefined||context.epoch===epoch)&&sameContext(context,contextOf(current));
export class PreviewGate{version=0;invalidate(){this.version++;}open(){return this.version;}accept(version){return version===this.version;}}
// Navigation and newer responses invalidate every older request, including POST.
export class ResponseGate {
  epoch=0; revision=-1;
  navigate(){this.epoch++;this.revision=-1;return this.epoch;}
  accept(epoch,state){if(epoch!==this.epoch||state.revision<this.revision)return false;this.revision=state.revision;return true;}
}
export function chartGeometry(points,width=720,height=180,reference=null,events=[]){
  const finite=p=>Number.isFinite(p.t)&&Number.isFinite(p.value);
  const timed=points.filter(p=>Number.isFinite(p.t));const valid=timed.filter(finite);
  if(!valid.length)return {segments:[],min:0,max:1,start:null,end:null};
  const eventTimes=events.filter(e=>Number.isFinite(e.t)).map(e=>e.t);
  const start=Math.min(...timed.map(p=>p.t),...eventTimes),end=Math.max(...timed.map(p=>p.t),...eventTimes);
  const hasReference=reference?.status==='available'&&Number.isFinite(reference.mean)&&Number.isFinite(reference.sd)&&reference.sd>=0;
  const bounds=hasReference?[reference.mean-2*reference.sd,reference.mean+2*reference.sd]:[];
  const min=Math.min(...valid.map(p=>p.value),...bounds),max=Math.max(...valid.map(p=>p.value),...bounds);
  const pad=Math.max((max-min)*.15,1);let segments=[],current=[];
  for(const p of points){if(!finite(p)){if(current.length)segments.push(current);current=[];continue;}
    current.push([52+(p.t-start)/(end-start||1)*(width-68),14+(max+pad-p.value)/(max-min+2*pad)*(height-42)]);}
  if(current.length)segments.push(current);
  const y=value=>14+(max+pad-value)/(max-min+2*pad)*(height-42),x=t=>52+(t-start)/(end-start||1)*(width-68);
  return {segments,min,max,start,end,domainMin:min-pad,domainMax:max+pad,reference:hasReference?{mean:y(reference.mean),bands:[2,1].map(sd=>({sd,top:y(reference.mean+sd*reference.sd),bottom:y(reference.mean-sd*reference.sd)}))}:null,events:events.filter(e=>Number.isFinite(e.t)).map(e=>({...e,x:x(e.t)})),ticks:[{value:max+pad,y:14},{value:(min+max)/2,y:14+(height-42)/2},{value:min-pad,y:height-28}]};
}
export function metricPoints(metric){return (metric.history||[]).map(p=>({t:Date.parse(p.observed_at),value:p.quality==='usable'?p.value:null}));}
export function validateDraft(action,p){
  const numeric=v=>v!==null&&v!==undefined&&String(v).trim()!==''&&Number.isFinite(Number(v))&&Number(v)>=0&&Number(v)<=10000;
  if(action==='capture'&&!numeric(p.value))return 'Enter a finite measurement from 0 to 10000 ms.';
  if(action==='author_reply'){
    const unit={reaction_time:'ms',heart_rate:'bpm',systolic_bp:'mmHg',diastolic_bp:'mmHg',spo2:'%',temperature:'C',sleep_duration:'h',sleep_quality:'score/10'}[p.metric||'reaction_time']||'ms';
    if(!['le','lt','gt','ge'].includes(p.operator)||!numeric(p.threshold))return `Choose a condition and a threshold from 0 to 10000 ${unit}.`;
    if(!p.original_reply?.trim()||p.original_reply.length>2000)return 'Write a reply of 1 to 2000 characters.';
    for(const key of ['checklist_a','checklist_b']){const lines=p[key]?.split('\n');if(!lines||lines.length<1||lines.length>6||lines.some(l=>!l.trim()||l.length>500))return 'Each checklist needs 1 to 6 nonempty lines, at most 500 characters per line.';}
  }
  if(action==='complete'&&(!p.note?.trim()||p.note.length>2000))return 'Write a completion note of 1 to 2000 characters.';
  return null;
}
export function authorTemplate(metric='reaction_time'){
  const presets={
    reaction_time:{operator:'ge',threshold:'500',original_reply:'Reviewed delivered reaction time. Re-verify onboard cognitive response and acquisition context upon arrival.',checklist_a:'Confirm quiet cabin environment and display focus\nRepeat five-trial reaction check\nRecord current shift workload and fatigue note',checklist_b:'Retain current reaction baseline\nRecord routine task readiness note'},
    heart_rate:{operator:'ge',threshold:'90',original_reply:'Reviewed delivered heart rate. Recheck resting pulse and sensor placement onboard.',checklist_a:'Rest seated for 5 minutes\nRepeat heart rate reading with same device\nRecord recent exertion or symptom context',checklist_b:'Retain routine cardiovascular schedule\nLog resting sensor check'},
    spo2:{operator:'le',threshold:'95',original_reply:'Reviewed delivered oxygen saturation. Verify oximeter placement and repeat reading.',checklist_a:'Check finger warmth and sensor contact\nRepeat SpO2 measurement after 2 minutes\nRecord breathing and cabin context',checklist_b:'Retain routine SpO2 log'},
    temperature:{operator:'ge',threshold:'37.5',original_reply:'Reviewed delivered temperature. Re-verify with same thermometer and record symptom context.',checklist_a:'Wait 10 minutes after oral intake\nRepeat temperature measurement with same device\nRecord intake assessment in Assistant',checklist_b:'Retain routine temperature log'},
  };
  return {metric,...(presets[metric]||{operator:'ge',threshold:'120',original_reply:`Reviewed delivered ${metric.replaceAll('_',' ')}. Re-verify measurement using the same source and method.`,checklist_a:`Confirm device placement and rest context\nRepeat ${metric.replaceAll('_',' ')} measurement\nRecord observation note`,checklist_b:'Retain routine monitoring schedule'})};
}
export function protocolGates(dep,digest=null){
  const r=dep?.reviewed,c=dep?.current,reason=String(c?.reason||'');
  const sameSensor=Boolean(c?.source_id&&r?.source_id&&c.source_id===r.source_id&&c?.method&&c.method===r?.method&&c.compatible!==false&&reason!=='incompatible dependency');
  const quality=c?.quality?c.quality==='usable':c?.usable===true;
  // Backend usable=true proves all dependency gates passed. Earlier failures
  // can mask the age check, so a timestamp by itself never proves freshness.
  const ageFailed=/stale|future|missing|conflicting/.test(reason);
  const fresh=c?.usable===true?true:ageFailed?false:null;
  return [
    {id:'sensor',label:sameSensor?'Source & method match':'Source / method missing or different',ok:sameSensor},
    {id:'quality',label:quality?'Recorded quality: usable':'Quality missing or needs review',ok:quality},
    {id:'freshness',label:fresh===true?'Backend freshness check passed':fresh===false?'Reading missing, stale or future':'Freshness not established',ok:fresh},
    {id:'digest',label:digest?`Snapshot SHA-256 ${String(digest).slice(0,8)} available`:'Snapshot hash unavailable',ok:digest?true:null},
  ];
}
export function inspectSignalFeatures(points=[]){
  const values=points.map(p=>p?.value).filter(v=>Number.isFinite(v));
  const gapCount=points.length-values.length;
  if(values.length<16)return null;
  const sorted=[...values].sort((a,b)=>a-b),min=sorted[0],max=sorted.at(-1),span=Math.max(max-min,1e-9);
  const mean=values.reduce((a,b)=>a+b,0)/values.length;
  const std=Math.max(Math.sqrt(values.reduce((a,v)=>a+(v-mean)**2,0)/values.length),1e-9);
  const diffs=values.slice(1).map((v,i)=>v-values[i]);
  const eps=Math.max(span*1e-6,1e-9);
  const flatFraction=diffs.filter(d=>Math.abs(d)<=eps).length/diffs.length;
  const edgeFraction=values.filter(v=>Math.abs(v-min)<=eps||Math.abs(v-max)<=eps).length/values.length;
  const derivativeRms=Math.sqrt(diffs.reduce((a,d)=>a+(d/std)**2,0)/diffs.length);
  const crestFactor=Math.max(...values.map(v=>Math.abs(v-mean)/std));
  const kurtosis=values.reduce((a,v)=>a+((v-mean)/std)**4,0)/values.length;
  const flagged=flatFraction>0.05||edgeFraction>0.1||derivativeRms>0.7||gapCount>0;
  return {sampleCount:points.length,gapCount,flatPct:Number((flatFraction*100).toFixed(1)),edgePct:Number((edgeFraction*100).toFixed(1)),derivativeRms:Number(derivativeRms.toFixed(2)),crestFactor:Number(crestFactor.toFixed(2)),kurtosis:Number(kurtosis.toFixed(2)),flagged,summary:flagged?'Technical artifact detected (flatline hold, rail clipping, or sample dropout) — review trace before relying on derived rate':'Waveform morphology nominal — no flatline hold or rail clipping detected'};
}
export function buildSampleSignal(subjectId,observedAt,variant='clean'){
  const rate=256,count=900,samples=[];
  for(let i=0;i<count;i++){
    const phase=(i%200)/200;
    const p=0.12*Math.exp(-(((phase-0.18)/0.045)**2));
    const q=-0.15*Math.exp(-(((phase-0.36)/0.018)**2));
    const r=1.15*Math.exp(-(((phase-0.40)/0.022)**2));
    const s=-0.25*Math.exp(-(((phase-0.44)/0.020)**2));
    const t=0.28*Math.exp(-(((phase-0.68)/0.060)**2));
    const wander=0.04*Math.sin(2*Math.PI*i/600)+0.008*Math.cos(2*Math.PI*i/37);
    samples.push(Number((p+q+r+s+t+wander).toFixed(6)));
  }
  if(variant==='corrupted'){
    const holdVal=samples[180];
    for(let i=180;i<360;i++)samples[i]=holdVal;
    for(let i=520;i<660;i++)samples[i]=Math.max(-0.08,Math.min(0.22,samples[i]));
    return {subject_id:subjectId,channel:'ECG',unit:'mV',sample_rate_hz:rate,samples,observed_at:observedAt,source_id:'onboard-ecg-corrupted-demo',method:'controlled corruption demo (flatline hold + clipping)',quality:'review',provenance:'synthetic'};
  }
  return {subject_id:subjectId,channel:'ECG',unit:'mV',sample_rate_hz:rate,samples,observed_at:observedAt,source_id:'sample-ecg-clean',method:'synthetic example waveform; not a person measurement',quality:'review',provenance:'synthetic'};
}
const expectedUnits={heart_rate:'bpm',systolic_bp:'mmHg',diastolic_bp:'mmHg',spo2:'%',temperature:'C',sleep_duration:'h',sleep_quality:'score/10'};
export function validateImportFile(action,filename,text,subjectId){
  if(action!=='import_observations'||!filename||typeof text!=='string')return null;
  if(!text.trim())return 'The selected file is empty. Include header columns and at least one observation row.';
  if(/\.csv$/i.test(filename)){
    const lines=text.replace(/\r\n/g,'\n').trim().split('\n');
    const headers=lines[0].split(',').map(h=>h.trim());
    const required=['subject_id','metric','value','unit','observed_at','source_id','method','quality'];
    const missing=required.filter(c=>!headers.includes(c));
    if(missing.length)return `CSV is missing required column(s): ${missing.join(', ')}. Required columns: ${required.join(', ')}.`;
    if(lines.length<2)return 'CSV contains column headers but no observation rows. Add at least one data row below the header.';
    const row=Object.fromEntries(headers.map((h,i)=>[h,(lines[1].split(',')[i]||'').trim()]));
    if(subjectId&&row.subject_id!==subjectId)return `Row 1 subject_id "${row.subject_id||'empty'}" does not match this crew session ("${subjectId}").`;
    if(!Object.hasOwn(expectedUnits,row.metric))return `Row 1 metric "${row.metric||'empty'}" is unsupported. Use one of: ${Object.keys(expectedUnits).join(', ')}.`;
    if(row.unit!==expectedUnits[row.metric])return `Row 1 metric "${row.metric}" requires unit "${expectedUnits[row.metric]}" (found "${row.unit||'empty'}").`;
    if(!['usable','review','missing'].includes(row.quality))return `Row 1 quality "${row.quality||'empty'}" must be usable, review, or missing.`;
  }
  return null;
}
