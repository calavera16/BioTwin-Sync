import React,{useEffect,useRef,useState} from 'react';
import {replayRequest,replayFrame,traceSegments,replayLimit} from './replay.mjs';
export function Signal({api,example,name,retry,clock,reduced,onReady,onRetry}){
  const [source,setSource]=useState(null),[error,setError]=useState('');
  const canvas=useRef(null),axis=useRef(null);
  useEffect(()=>{let active=true;const controller=new AbortController();setSource(null);setError('');onReady(example,null);api(replayRequest(example),{signal:controller.signal}).then(data=>{if(active){setSource(data);onReady(example,replayLimit(data));}}).catch(e=>{if(active&&e.name!=='AbortError')setError(e.message);});return()=>{active=false;controller.abort();};},[api,example,retry]);
  useEffect(()=>{if(!source)return;let frameId,lastClock=null,lastWidth=null,lastHeight=null;const draw=()=>{
    const node=canvas.current;if(!node)return;const rect=node.getBoundingClientRect(),ratio=window.devicePixelRatio||1;
    const w=Math.max(1,rect.width),h=Math.max(1,rect.height),displayClock=reduced?Math.floor(clock.current):clock.current;
    if(displayClock===lastClock&&w===lastWidth&&h===lastHeight){frameId=requestAnimationFrame(draw);return;}lastClock=displayClock;lastWidth=w;lastHeight=h;
    if(node.width!==Math.round(w*ratio)||node.height!==Math.round(h*ratio)){node.width=Math.round(w*ratio);node.height=Math.round(h*ratio);}
    const ctx=node.getContext('2d');if(!ctx){axis.current.textContent='Waveform display unavailable in this browser.';return;}ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);ctx.strokeStyle='#203c30';ctx.lineWidth=.6;ctx.beginPath();for(let x=0;x<w;x+=50){ctx.moveTo(x,0);ctx.lineTo(x,h);}for(let y=0;y<h;y+=25){ctx.moveTo(0,y);ctx.lineTo(w,y);}ctx.stroke();
    const f=replayFrame(source,clock.current,reduced);ctx.strokeStyle=name==='ECG'?'#a2edcc':'#c6eedc';ctx.lineWidth=1.3;ctx.beginPath();
    for(const segment of traceSegments(source,f))segment.forEach((p,i)=>{const x=(p.t-f.start)/(f.end-f.start||1)*w,y=h-10-(p.value-f.min)/(f.max-f.min||1)*(h-20);if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);});ctx.stroke();
    axis.current.textContent=`${f.start.toFixed(2)}-${f.end.toFixed(2)} s / original source time`;
    frameId=requestAnimationFrame(draw);
  };frameId=requestAnimationFrame(draw);return()=>cancelAnimationFrame(frameId);},[source,clock,reduced,name]);
  return <section className={'mission-signal '+(name==='ECG'?'ecg':'bvp')} aria-label={`${name} NASA source recording`}><div className="mission-signal-heading"><h2>{name}</h2><span>{name==='ECG'?'Electrical activity':'Pulse waveform / BVP'}</span><b>RECORDED SOURCE</b></div>{error?<div className="mission-source-state" role="alert"><strong>Recording unavailable</strong><p>{error}</p><button onClick={onRetry}>Retry recordings</button></div>:!source?<div className="mission-source-state" role="status">Loading original NASA recording...</div>:<><canvas ref={canvas} className="mission-trace" role="img" aria-label={`${name} recorded waveform. Fixed amplitude scale; missing samples and timestamp gaps preserved.`}/><div className="mission-signal-axis"><span ref={axis}>Original source time</span><span>Fixed amplitude scale</span></div><details className="mission-channel-details"><summary>Source identity & acquisition</summary><p>{source.source_key}<br/>{source.unit} / nominal {source.nominal_rate_hz} Hz<br/>{source.raw_sample_count} original samples; {source.preview_method} for display. {source.quality_reasons.join('; ')}<br/>Synchronization unverified. Missing samples and timestamp gaps remain gaps.</p><a href={source.doi} target="_blank" rel="noreferrer">Study provenance / DOI</a></details></>}</section>;
}
