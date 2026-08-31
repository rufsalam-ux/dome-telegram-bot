import {useCallback} from 'react';

import {playExperience,type ExperienceFeedbackOptions,type ExperienceEvent} from './experience';

export type DomeFeedbackEvent='tap'|'primary'|'success'|'wrong'|'dragStart'|'drop'|'recordStart'|'recordStop'|'next';

export const DOME_FEEDBACK_EVENT_MAP:Record<DomeFeedbackEvent,ExperienceEvent>={
  tap:'BUTTON_TAP',
  primary:'BUTTON_CONTINUE',
  success:'CORRECT',
  wrong:'TRY_AGAIN',
  dragStart:'DRAG_PICKUP',
  drop:'DROP_CORRECT',
  recordStart:'RECORDING_START',
  recordStop:'RECORDING_STOP',
  next:'BUTTON_CONTINUE',
};

export function emitDomeFeedback(event:DomeFeedbackEvent,options:ExperienceFeedbackOptions={}):void{
  playExperience(DOME_FEEDBACK_EVENT_MAP[event],options);
}

export function useDomeFeedback(){
  return useCallback((event:DomeFeedbackEvent,options:ExperienceFeedbackOptions={})=>emitDomeFeedback(event,options),[]);
}
