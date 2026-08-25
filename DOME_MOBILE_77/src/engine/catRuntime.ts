import type {RuntimeStage} from './lessonRuntime';

export type CatActivityState='idle'|'listening'|'thinking'|'happy'|'encouraging'|'surprised'|'waiting'|'playing'|'sleeping';
export const CAT_ACTIVITY_STATES:CatActivityState[]=['idle','listening','thinking','happy','encouraging','surprised','waiting','playing','sleeping'];

export function catStateForStage(stage:RuntimeStage):CatActivityState{
  if(stage==='AI_SPEAKING')return 'listening';
  if(stage==='PROCESSING')return 'thinking';
  if(stage==='RETRY')return 'encouraging';
  if(stage==='FEEDBACK'||stage==='COMPLETE')return 'happy';
  if(stage==='WAITING_ACTION'||stage==='WAITING_VOICE')return 'waiting';
  return 'idle';
}

export function catProcessingState(elapsedMs:number):CatActivityState{
  if(elapsedMs<1500)return 'thinking';
  if(elapsedMs<4000)return 'idle';
  return 'waiting';
}
