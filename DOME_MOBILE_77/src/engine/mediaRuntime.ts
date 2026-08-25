export type LessonMediaType='image'|'video'|'animation'|'youtube'|'audio';

export type LessonMediaDescriptor={
  id:string;
  type:LessonMediaType;
  src?:string;
  url?:string;
  animation_id?:string;
  autoplay?:boolean;
  advance_on_end?:boolean;
  poster?:string;
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
