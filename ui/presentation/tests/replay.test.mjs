import test from 'node:test';
import assert from 'node:assert/strict';
import {storyRecordingOffset,advanceReplay,replayFrame,replayLimit,replayRequest,traceSegments} from '../src/replay.mjs';
const source={source_start:700,source_end:760,amplitude_min:-5,amplitude_max:20,gap_intervals:[[715,718]],points:Array.from({length:61},(_,i)=>({t:700+i,value:i===14?null:i%5}))};
test('successive recorded windows advance with fixed scale and no wrap',()=>{
 const first=replayFrame(source,0),later=replayFrame(source,12.5);
 assert.equal(first.start,700);assert.equal(first.end,710);assert.equal(later.start,712.5);
 assert.notDeepEqual(first.points,later.points);assert.equal(first.min,later.min);assert.equal(first.max,later.max);
 assert.equal(replayLimit(source),50);assert.equal(advanceReplay(49,8,true,50),50);assert.equal(advanceReplay(50,8,true,50),50);
 assert.equal(advanceReplay(12,8,false,50),12);assert.equal(advanceReplay(12,-8,true,50),12);
 assert.equal(replayFrame(source,100).end,760);assert.equal(replayFrame(source,12.7,true).start,712);
});
test('nulls and ORIGINAL timestamp gaps break traces; decimated cadence does not',()=>{
 const sample={...source,points:[{t:710,value:1},{t:713,value:2},{t:714,value:null},{t:715,value:4},{t:718,value:2},{t:720,value:3}]};
 assert.deepEqual(traceSegments(sample,replayFrame(sample,10)).map(s=>s.map(p=>p.t)),[[710,713],[715],[718,720]]);
 assert.equal(traceSegments(source,{points:[]}).length,0);
 assert.equal(replayRequest('crew001_ecg.json'),'/api/source-replay?example=crew001_ecg.json');
});

test('story seeks use the same 1x source-time mapping as playback',()=>{assert.equal(storyRecordingOffset(15,50),15);assert.equal(storyRecordingOffset(30,20),20);assert.equal(storyRecordingOffset(-3,50),0);});
