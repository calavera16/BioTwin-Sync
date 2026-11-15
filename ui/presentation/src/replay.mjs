// Recorded samples only. The viewport moves; the recording never loops.
export const VIEW_SECONDS=10;
export const replayRequest=example=>'/api/source-replay?'+new URLSearchParams({example});
export function replayLimit(source){return Math.max(0,(source?.source_end??0)-(source?.source_start??0)-VIEW_SECONDS);}
export function advanceReplay(value,elapsed,playing,limit){return Math.max(0,Math.min(limit,value+(playing?Math.max(0,elapsed):0)));}
export function replayFrame(source,elapsed,reduced=false){
  const offset=Math.min(replayLimit(source),Math.max(0,reduced?Math.floor(elapsed):elapsed));
  const start=source.source_start+offset,end=Math.min(source.source_end,start+VIEW_SECONDS);
  const points=source.points.filter(p=>p.t>=start&&p.t<=end);
  return {start,end,points,min:source.amplitude_min,max:source.amplitude_max};
}
export function traceSegments(source,frame){
  const segments=[];let segment=[],previous=null,gapIndex=0;const gaps=source.gap_intervals||[];
  for(const p of frame.points){
    while(gapIndex<gaps.length&&previous!==null&&gaps[gapIndex][1]<=previous)gapIndex++;
    const gap=previous!==null&&gapIndex<gaps.length&&previous<=gaps[gapIndex][0]&&p.t>=gaps[gapIndex][1];
    if(!Number.isFinite(p.value)||gap){if(segment.length)segments.push(segment);segment=[];}
    if(Number.isFinite(p.value))segment.push(p);
    previous=p.t;
  }
  if(segment.length)segments.push(segment);
  return segments;
}

export function storyRecordingOffset(seconds,limit){return Math.max(0,Math.min(limit,seconds));}
