export type AvatarFacing='left'|'right'|'front';
export type SourceAvatarFacing='LEFT'|'RIGHT'|'FRONT'|'UNKNOWN';

type AvatarSlideLayout={
  hero_anchor?:string;
  hero_placement?:string;
  hero_box?:number[];
  hero_fallback_anchors?:string[];
  hero_facing?:AvatarFacing;
  hero_target_visual_height_ratio?:number;
  hero_min_visual_height_ratio?:number;
};

const DEMO_001_LAYOUT:{lesson:any;slides:Record<string,AvatarSlideLayout>}={
  lesson:{
    default_hero_placement:'right',
    hero_layout:{
      anchors:{
        left:[0.02,0.28,0.26,0.68],
        right:[0.72,0.28,0.26,0.68],
        bottom_left:[0.02,0.42,0.27,0.55],
        bottom_right:[0.71,0.42,0.27,0.55],
        left_of_mila:[0.30,0.34,0.23,0.61],
      },
      fallback_order:['left','right','bottom_left','bottom_right'],
      target_visual_height_ratio:.72,
      min_visual_height_ratio:.56,
    },
  },
  slides:{
    slide_01:{hero_anchor:'bottom_right',hero_box:[0.70,0.44,0.28,0.54],hero_fallback_anchors:['bottom_left'],hero_facing:'left',hero_target_visual_height_ratio:.54,hero_min_visual_height_ratio:.5},
    slide_19:{hero_anchor:'right',hero_box:[0.42,0.25,0.28,0.68],hero_fallback_anchors:['right'],hero_facing:'left',hero_target_visual_height_ratio:.68,hero_min_visual_height_ratio:.64},
    slide_20:{hero_anchor:'left_of_mila',hero_box:[0.25,0.25,0.29,0.70],hero_fallback_anchors:['left'],hero_facing:'right',hero_target_visual_height_ratio:.70,hero_min_visual_height_ratio:.66},
  },
};

function isDemo001(lessonId:any):boolean{return String(lessonId||'demo_001')==='demo_001'}

export function lessonAvatarConfig(lesson:any):any{
  if(!isDemo001(lesson?.lesson_id))return lesson;
  return {...lesson,...DEMO_001_LAYOUT.lesson,hero_layout:{...(lesson?.hero_layout||{}),...DEMO_001_LAYOUT.lesson.hero_layout,anchors:{...(lesson?.hero_layout?.anchors||{}),...DEMO_001_LAYOUT.lesson.hero_layout.anchors}}};
}

export function slideAvatarConfig(slide:any,lessonId:any):any{
  if(!slide||!isDemo001(lessonId))return slide;
  return {...slide,...(DEMO_001_LAYOUT.slides[String(slide.slide_id)]||{})};
}

export function canonicalChildAvatarUri(child:any,apiBase:string):string|undefined{
  const value=String(child?.heroUrl||'').trim();
  if(!value)return undefined;
  return /^https?:\/\//i.test(value)?value:`${String(apiBase||'').replace(/\/$/,'')}${value.startsWith('/')?'':'/'}${value}`;
}

export function avatarFacing(slide:any,lesson:any):AvatarFacing{
  const explicit=String(slide?.hero_facing||'') as AvatarFacing;
  if(['left','right','front'].includes(explicit))return explicit;
  const anchor=String(slide?.hero_anchor||slide?.hero_placement||lesson?.default_hero_placement||'right');
  if(anchor.includes('left'))return 'right';
  if(anchor.includes('right'))return 'left';
  return 'front';
}

export function sourceAvatarFacing(metadata:any):SourceAvatarFacing{
  const value=String(metadata?.facingDirection||'UNKNOWN').toUpperCase() as SourceAvatarFacing;
  return ['LEFT','RIGHT','FRONT'].includes(value)?value:'UNKNOWN';
}

export function avatarScaleX(desired:AvatarFacing,source:SourceAvatarFacing='UNKNOWN'):number{
  if(desired==='front'||source==='FRONT')return 1;
  if(source==='LEFT')return desired==='left'?1:-1;
  if(source==='RIGHT')return desired==='right'?1:-1;
  return desired==='left'?-1:1;
}

export function visibleCharacterBox(metadata:any):[number,number,number,number]{
  const raw=metadata?.characterBoundingBox;const fallback:[number,number,number,number]=[0,0,1,1];
  if(!Array.isArray(raw)||raw.length!==4)return fallback;
  const left=Number(raw[0]);const top=Number(raw[1]);const width=Number(raw[2]);const height=Number(raw[3]);
  if(![left,top,width,height].every(Number.isFinite)||width<=0||height<=0)return fallback;
  return [Math.max(0,left),Math.max(0,top),Math.min(1,width),Math.min(1,height)];
}
