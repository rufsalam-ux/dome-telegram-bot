import {useCallback} from 'react';

import {playExperience,type ExperienceFeedbackOptions,type ExperienceEvent} from './experience';

export type DomeFeedbackEvent='tap'|'primary'|'success'|'wrong'|'dragStart'|'dragTexture'|'drop'|'invalidDrop'|'recordStart'|'recordStop'|'next';

export const DRAG_FEEDBACK_THROTTLE_MS=120;
let lastDragFeedbackAt=0;

export const DOME_FEEDBACK_EVENT_MAP:Record<DomeFeedbackEvent,ExperienceEvent>={
  tap:'BUTTON_TAP',
  primary:'BUTTON_CONTINUE',
  success:'CORRECT',
  wrong:'TRY_AGAIN',
  dragStart:'DRAG_PICKUP',
  dragTexture:'DRAG_TEXTURE',
  drop:'DROP_CORRECT',
  invalidDrop:'DROP_INVALID',
  recordStart:'RECORDING_START',
  recordStop:'RECORDING_STOP',
  next:'BUTTON_CONTINUE',
};

export function emitDomeFeedback(event:DomeFeedbackEvent,options:ExperienceFeedbackOptions={}):void{
  playExperience(DOME_FEEDBACK_EVENT_MAP[event],options);
}

export function emitDragTextureFeedback(now=Date.now()):boolean{
  if(now-lastDragFeedbackAt<DRAG_FEEDBACK_THROTTLE_MS)return false;
  lastDragFeedbackAt=now;
  emitDomeFeedback('dragTexture');
  return true;
}

export function useDomeFeedback(){
  return useCallback((event:DomeFeedbackEvent,options:ExperienceFeedbackOptions={})=>emitDomeFeedback(event,options),[]);
}
