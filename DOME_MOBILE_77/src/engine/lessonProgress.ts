export type CompletionPhase='ACTIVE'|'COMPLETING'|'COMPLETED';

export type CompletionRecovery={
  phraseId:string;
  stepId:string;
  returnTo:'COMPLETE';
};

export function resolveResumeIndex(slides:any[],session:any,lessonVersion:string):number{
  if(!slides.length)return 0;
  const version=String(session?.lesson_version||'');
  const stableId=String(session?.current_step_id||'');
  if(version&&version===lessonVersion&&stableId){
    const stableIndex=slides.findIndex(item=>String(item?.slide_id||item?.id||'')===stableId);
    if(stableIndex>=0)return stableIndex;
  }
  // A positional index is safe only when the server explicitly confirms the
  // same immutable content version.  Never apply an old index to a new route.
  if(version&&version===lessonVersion){
    return Math.min(Math.max(Number(session?.current_step)||0,0),slides.length-1);
  }
  return 0;
}

export function completionRecoveryFromError(error:any,slides:any[]):CompletionRecovery|undefined{
  const declared=Array.isArray(error?.data?.missing_steps)?error.data.missing_steps:[];
  const first=declared.find((item:any)=>item&&item.phrase_id&&item.step_id);
  if(first&&slides.some(item=>String(item?.slide_id||item?.id||'')===String(first.step_id))){
    return {phraseId:String(first.phrase_id),stepId:String(first.step_id),returnTo:'COMPLETE'};
  }
  const phrases=Array.isArray(error?.data?.missing_phrase_ids)?error.data.missing_phrase_ids.map(String):[];
  const fallback=slides.find(item=>phrases.includes(String(item?.required_phrase_id||item?.moviePhraseId||'')));
  return fallback?{phraseId:String(fallback.required_phrase_id||fallback.moviePhraseId),stepId:String(fallback.slide_id||fallback.id),returnTo:'COMPLETE'}:undefined;
}

export function completionRecoveryFromSession(session:any,slides:any[]):CompletionRecovery|undefined{
  const raw=session?.completion_recovery;
  if(!raw||String(raw.return_to||raw.returnTo||'')!=='COMPLETE')return undefined;
  const phraseId=String(raw.phrase_id||(Array.isArray(raw.phrase_ids)?raw.phrase_ids[0]:'')||'');
  const stepId=String(raw.step_id||'');
  return phraseId&&stepId&&slides.some(item=>String(item?.slide_id||item?.id||'')===stepId)?{phraseId,stepId,returnTo:'COMPLETE'}:undefined;
}

export function recoveryReturnsDirectlyToCompletion(recovery:CompletionRecovery|undefined,currentStepId:string):boolean{
  return Boolean(recovery&&recovery.returnTo==='COMPLETE'&&recovery.stepId===String(currentStepId||''));
}
