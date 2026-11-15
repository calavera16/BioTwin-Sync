import {contextOf} from './model.mjs';

export const guideScenes=Object.freeze([
  {title:'Choose your crew',text:'Each crew has separate observations and received messages. Switch crew in the selector above the monitor.'},
  {title:'Understand the change',text:'Compare the measurement with its own comparable history. A personal reference describes earlier observations; it is not a health range.'},
  {title:'Take the received next step',text:'Open Self-check, read a received instruction and perform it. Record completion with an explicit note, then send follow-up when available.'},
  {title:'Read across the delay',text:'Earth sees delivered snapshots only. A reply can refer to an older observation after 20 simulated minutes each way.'},
]);
export const demoScenes=Object.freeze([
  {seconds:0,time:'09:00',title:'Consultation sent',icon:'send',text:'An onboard measurement leaves the spacecraft. Earth has not received it yet.',plain:'Step 1 of 4: The astronaut records a baseline 420 ms reaction time and sends it to Earth. Because of the 20-minute deep-space radio delay, Earth still has no data yet.'},
  {seconds:1200,time:'09:20',title:'Earth reviews',icon:'earth',text:'The earlier snapshot arrives. A conditional measurement checklist begins its return journey.',plain:'Step 2 of 4: Earth receives the 420 ms reading after 20 minutes. Knowing the astronaut might change while the reply travels back, the Flight Surgeon sends an IF/ELSE conditional checklist.'},
  {seconds:1800,time:'09:30',title:'Evidence changes',icon:'pulse',text:'Crew has a newer measurement. Earth still sees the earlier delivered snapshot.',plain:'Step 3 of 4: While Earth’s reply is still in transit through space, the astronaut’s reaction time slows to 585 ms onboard. Earth still only sees the older 420 ms reading.'},
  {seconds:2400,time:'09:40',title:'Reply arrives',icon:'checklist',text:'Onboard evidence selects the applicable branch of the received checklist.',plain:'Step 4 of 4: Earth’s conditional checklist arrives and automatically evaluates against the newest 585 ms onboard reading—activating Branch B without waiting another 40 minutes.'},
]);
export const guideIndex=index=>Math.max(0,Math.min(guideScenes.length-1,Number.isFinite(index)?Math.trunc(index):0));
export const stepTitle=step=>{const text=typeof step==='string'?step:JSON.stringify(step);return text.length>66?text.slice(0,63)+'…':text;};
export function stepKeyIndex(index,count,key){if(!count)return null;return key==='Home'?0:key==='End'?count-1:key==='ArrowDown'||key==='ArrowRight'?Math.min(count-1,index+1):key==='ArrowUp'||key==='ArrowLeft'?Math.max(0,index-1):null;}
// Local selection only. Never dispatch an API action when browsing instructions.
export const checklistKey=state=>JSON.stringify([contextOf(state),state.projection.checklist.branch,state.projection.checklist.steps]);
export function stepSelection(state,index){
  const count=state.projection.checklist.steps.length;
  return {key:checklistKey(state),index:count?Math.max(0,Math.min(count-1,Number.isFinite(index)?Math.trunc(index):0)):null};
}
export function selectedStep(state,selection){
  const c=state.projection.checklist;
  if(!c.steps.length)return null;
  if(selection?.key===checklistKey(state))return stepSelection(state,selection.index).index;
  const next=c.steps.findIndex((_,i)=>!(c.completed_steps||[]).includes(i));
  return next<0?0:next;
}
export function completionReason(state,index,busy=false){
  if(state.role==='doctor')return 'Flight-surgeon view is read only. Completion is recorded onboard.';
  if(state.mode==='guided')return 'Synthetic guided fixture. Browsing does not record completion.';
  if(busy)return 'Another action is being submitted. Please wait.';
  if(!['local','operator'].includes(state.mode)||index==null||!state.actions.complete)return 'No applicable completion action is available for this evidence.';
  if((state.projection.checklist.completed_steps||[]).includes(index))return 'Completion is already recorded for this step.';
  return '';
}
