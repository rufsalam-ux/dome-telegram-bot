export type LessonMediaType='image'|'video'|'animation'|'youtube'|'audio';

export type LessonMediaDescriptor={
  id:string;
  type:LessonMediaType;
  src?:string;
  url?:string;
  animation_id?:string;
  autoplay?:boolean;
  auto_continue?:boolean;
  advance_on_end?:boolean;
  skippable?:boolean;
  replay?:boolean;
  poster?:string;
  aspect_ratio?:number|string;
};

const MEDIA_TYPES=new Set<LessonMediaType>(['image','video','animation','youtube','audio']);

export function normalizeMediaSequence(slide:any):LessonMediaDescriptor[]{
  const configured=Array.isArray(slide?.media_sequence)?slide.media_sequence:[];
  if(configured.length){
    return configured.map((item:any,index:number)=>({
      ...item,
      id:String(item?.id||`media_${index+1}`),
      type:String(item?.type||'') as LessonMediaType,
      src:String(item?.src||item?.url||''),
    })).filter((item:LessonMediaDescriptor)=>MEDIA_TYPES.has(item.type));
  }
  if(slide?.image||slide?.image_file)return [{id:'visual',type:'image',src:String(slide.image||slide.image_file)}];
  if(slide?.video_file||slide?.video_url){const src=String(slide.video_file||slide.video_url);return [{id:'video',type:/youtu(?:\.be|be\.com)/i.test(src)?'youtube':'video',src}]}
  if(slide?.audio_file||slide?.audio_url)return [{id:'audio',type:'audio',src:String(slide.audio_file||slide.audio_url)}];
  return [];
}

export function mediaPhaseAfterEnd(sequence:LessonMediaDescriptor[],index:number):number{
  if(!sequence.length)return 0;
  return Math.min(Math.max(0,index+1),sequence.length-1);
}

export function usesGenericMediaRuntime(slide:any):boolean{
  const sequence=normalizeMediaSequence(slide);
  return sequence.length>1||sequence.some(item=>item.type!=='image');
}

export type VideoStepBehavior={autoplay:boolean;autoContinue:boolean;skippable:boolean;replay:boolean;aspectRatio:number};

export function isStandaloneVideoStep(slide:any):boolean{
  return String(slide?.authoring_type||slide?.type||'').toLowerCase()==='video';
}

export function videoStepBehavior(slide:any):VideoStepBehavior{
  const item=normalizeMediaSequence(slide).find(media=>media.type==='video');
  const rawAspect=item?.aspect_ratio??slide?.aspect_ratio??slide?.aspectRatio;
  let aspectRatio=16/9;
  if(typeof rawAspect==='number'&&Number.isFinite(rawAspect)&&rawAspect>0)aspectRatio=rawAspect;
  else if(typeof rawAspect==='string'){
    const match=rawAspect.trim().match(/^(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)$/);
    const numeric=Number(rawAspect);
    if(match&&Number(match[2])>0)aspectRatio=Number(match[1])/Number(match[2]);
    else if(Number.isFinite(numeric)&&numeric>0)aspectRatio=numeric;
  }
  return {
    autoplay:(item?.autoplay??slide?.autoplay)!==false,
    autoContinue:(item?.auto_continue??slide?.autoContinue??slide?.auto_continue)!==false,
    skippable:(item?.skippable??slide?.skippable)!==false,
    replay:(item?.replay??slide?.replay)!==false,
    aspectRatio:Math.max(.45,Math.min(2.4,aspectRatio)),
  };
}
