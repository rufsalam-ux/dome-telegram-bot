export type PreSlideVideoPolicy='every_attempt'|'once_per_attempt'|'once_ever';
export type PreSlideVideoDescriptor={uri:string;enabled:true;skippable:boolean;showPolicy:PreSlideVideoPolicy;autoplay:boolean};
export type PreSlideVideoState={attempt:string[];ever:string[]};

const POLICIES=new Set<PreSlideVideoPolicy>(['every_attempt','once_per_attempt','once_ever']);

export function normalizePreSlideVideo(slide:any):PreSlideVideoDescriptor|null{
  const raw=slide?.preSlideVideo||slide?.pre_slide_video;if(!raw||raw.enabled===false)return null;
  const uri=String(raw.uri||raw.src||raw.url||'').trim();if(!uri)return null;
  const requested=String(raw.showPolicy||raw.show_policy||'once_per_attempt') as PreSlideVideoPolicy;
  return {uri,enabled:true,skippable:raw.skippable!==false,showPolicy:POLICIES.has(requested)?requested:'once_per_attempt',autoplay:raw.autoplay!==false};
}

export function preSlideVideoKey(slide:any,video:PreSlideVideoDescriptor):string{
  return `${String(slide?.slide_id||'slide')}:${video.uri}`;
}

export function shouldShowPreSlideVideo(slide:any,state:PreSlideVideoState):boolean{
  const video=normalizePreSlideVideo(slide);if(!video)return false;const key=preSlideVideoKey(slide,video);
  if((state.attempt||[]).includes(key))return false;
  return video.showPolicy!=='once_ever'||!(state.ever||[]).includes(key);
}

export function markPreSlideVideoShown(state:PreSlideVideoState,key:string):PreSlideVideoState{
  return {attempt:Array.from(new Set([...(state.attempt||[]),key])),ever:Array.from(new Set([...(state.ever||[]),key]))};
}

export function preSlideVideoTargetIndex(currentIndex:number,slides:any[],state:PreSlideVideoState):{nextIndex:number;video:PreSlideVideoDescriptor|null;key?:string}{
  const nextIndex=Math.min(currentIndex+1,Math.max(0,slides.length-1));const slide=slides[nextIndex];const video=shouldShowPreSlideVideo(slide,state)?normalizePreSlideVideo(slide):null;
  return {nextIndex,video,...(video?{key:preSlideVideoKey(slide,video)}:{})};
}
