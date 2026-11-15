// Clock and randomness are injected so task behavior can be checked deterministically.
// The view calls showCue after committing the actual cue, never at scheduling time.
export class BrowserReaction {
  constructor(clock=()=>performance.now(),random=()=>crypto.getRandomValues(new Uint32Array(1))[0]/4294967296){this.clock=clock;this.random=random;this.phase='new';this.trials=[];this.last=-Infinity;}
  now(){const n=this.clock();if(!Number.isFinite(n)||n<this.last)throw Error('Timing clock changed. Restart the check.');this.last=n;return n;}
  start(){if(this.phase!=='new')throw Error('Check already started.');this.started=this.now();this.wait();}
  wait(){if(this.trials.length===5){this.phase='finished';return;}this.due=this.now()+1000+this.random()*2000;this.phase='waiting';}
  tick(){if(['new','finished','cancelled'].includes(this.phase))return this.phase;const n=this.now();if(n-this.started>=60000){this.cancel('The check exceeded its time limit.');return this.phase;}if(this.phase==='cue'&&n-this.onset>=2000){this.trials.push({outcome:'omission',reaction_ms:null});this.wait();}else if(this.phase==='waiting'&&n>=this.due)this.phase='drawing';return this.phase;}
  showCue(){if(this.phase==='drawing'){this.onset=this.now();this.phase='cue';}}
  respond(){const count=this.trials.length;this.tick();if(count!==this.trials.length)return;if(!['waiting','drawing','cue'].includes(this.phase))return;const ms=this.phase==='cue'?this.now()-this.onset:null;this.trials.push({outcome:ms===null||ms<100?'false_start':'response',reaction_ms:ms});this.wait();}
  cancel(reason='Check cancelled.'){if(this.phase!=='finished'){this.phase='cancelled';this.reason=reason;}}
  summary(){if(this.phase!=='finished')throw Error('Only a completed check can be saved.');const values=this.trials.filter(t=>t.outcome==='response').map(t=>t.reaction_ms).sort((a,b)=>a-b);const m=Math.floor(values.length/2);return {task_id:'browser-simple-reaction-v1',value:values.length?(values.length%2?values[m]:(values[m-1]+values[m])/2):null,unit:'ms',quality:values.length===5?'usable':values.length?'review':'missing',trials:this.trials.map(t=>({...t})),valid_responses:values.length,false_starts:this.trials.filter(t=>t.outcome==='false_start').length,omissions:this.trials.filter(t=>t.outcome==='omission').length,configuration:{trials:5,foreperiod_seconds:[1,3],response_limit_seconds:2,minimum_response_ms:100,input_mode:'keyboard-space'},timing_clock:'performance.now',focus_visibility_guard:true,cancelled:false};}
}
