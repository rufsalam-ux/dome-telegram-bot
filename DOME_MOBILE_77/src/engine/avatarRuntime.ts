export type AvatarFacing='left'|'right'|'front';
export type SourceAvatarFacing='LEFT'|'RIGHT'|'FRONT'|'UNKNOWN';

type AvatarSlideLayout={
  hero_visibility?:'scene'|'hidden';
  hero_anchor?:string;
  hero_placement?:string;
  hero_box?:number[];
  hero_fallback_anchors?:string[];
  hero_facing?:AvatarFacing;
  hero_target_visual_height_ratio?:number;
  hero_min_visual_height_ratio?:number;
  protected_character_boxes?:number[][];
  content_boxes?:number[][];
};

const DEMO_001_LAYOUT:{lesson:any;slides:Record<string,AvatarSlideLayout>}={
  lesson:{
    default_hero_placement:'right',
    hero_layout:{
      anchors:{
        left:[0.01,0.27,0.48,0.69],
        right:[0.51,0.27,0.48,0.69],
        bottom_left:[0.01,0.42,0.48,0.54],
        bottom_right:[0.51,0.42,0.48,0.54],
        left_of_lyosha:[0.005,0.32,0.397,0.52],
        left_of_mila:[0.01,0.40,0.548,0.55],
      },
      fallback_order:['left','right','bottom_left','bottom_right'],
      target_visual_height_ratio:.69,
      min_visual_height_ratio:.36,
    },
  },
  slides:{
    slide_01:{hero_visibility:'scene',hero_anchor:'bottom_right',hero_box:[0.62,0.48,0.36,0.48],hero_fallback_anchors:['bottom_left'],hero_facing:'left',hero_target_visual_height_ratio:.48,hero_min_visual_height_ratio:.4},
    slide_03:{hero_visibility:'scene',hero_anchor:'left',hero_box:[0.02,0.43,0.26,0.50],hero_facing:'right',protected_character_boxes:[[0.30,0.34,0.39,0.58]]},
    slide_09:{hero_visibility:'hidden'},
    slide_04:{hero_visibility:'scene',hero_anchor:'left',hero_box:[0.02,0.43,0.24,0.50],hero_facing:'right',protected_character_boxes:[[0.28,0.28,0.55,0.64]]},
    slide_06:{hero_visibility:'scene',hero_anchor:'left',hero_box:[0.02,0.48,0.21,0.46],hero_facing:'right',protected_character_boxes:[[0.25,0.35,0.72,0.58]]},
    slide_07:{hero_visibility:'hidden'},
    slide_08:{hero_visibility:'hidden'},
    slide_17:{hero_visibility:'scene',hero_anchor:'right',hero_box:[0.76,0.38,0.22,0.56],hero_facing:'left',protected_character_boxes:[[0.22,0.22,0.55,0.66]]},
    slide_18:{hero_visibility:'hidden'},
    slide_19:{hero_visibility:'scene',hero_anchor:'left_of_lyosha',hero_box:[0.005,0.32,0.397,0.52],hero_fallback_anchors:[],hero_facing:'right',hero_target_visual_height_ratio:.52,hero_min_visual_height_ratio:.38,protected_character_boxes:[[0.42,0.28,0.24,0.56]]},
    slide_20:{hero_visibility:'scene',hero_anchor:'left_of_mila',hero_box:[0.01,0.40,0.548,0.55],hero_fallback_anchors:[],hero_facing:'right',hero_target_visual_height_ratio:.55,hero_min_visual_height_ratio:.40,protected_character_boxes:[[0.58,0.34,0.22,0.61]]},
    slide_21:{hero_visibility:'scene',hero_anchor:'right',hero_box:[0.74,0.43,0.24,0.51],hero_facing:'left',protected_character_boxes:[[0.26,0.38,0.46,0.56]],content_boxes:[[0.16,0.03,0.66,0.30]]},
    slide_22:{hero_visibility:'hidden'},
    slide_23:{hero_visibility:'hidden'},
    slide_24:{hero_visibility:'hidden'},
    slide_40:{hero_visibility:'hidden'},
    slide_41:{hero_visibility:'hidden'},
    slide_47:{hero_visibility:'scene',hero_anchor:'right',hero_box:[0.68,0.43,0.30,0.52],hero_facing:'left',protected_character_boxes:[[0.28,0.26,0.35,0.66]]},
    slide_50:{hero_visibility:'scene',hero_anchor:'right',hero_box:[0.68,0.43,0.30,0.52],hero_facing:'left',protected_character_boxes:[[0.35,0.26,0.31,0.66]]},
    slide_46:{hero_visibility:'hidden'},
    slide_51:{hero_visibility:'hidden'},
    slide_45:{hero_visibility:'scene',hero_anchor:'left',hero_box:[0.03,0.43,0.34,0.52],hero_facing:'right',protected_character_boxes:[[0.43,0.20,0.25,0.75]]},
    slide_42:{hero_visibility:'scene',hero_anchor:'right',hero_box:[0.68,0.43,0.30,0.52],hero_facing:'left',protected_character_boxes:[[0.34,0.27,0.33,0.66]]},
    slide_44:{hero_visibility:'scene',hero_anchor:'left',hero_box:[0.03,0.43,0.30,0.52],hero_facing:'right',protected_character_boxes:[[0.37,0.18,0.32,0.76]]},
    slide_48:{hero_visibility:'hidden'},
    slide_16:{hero_visibility:'hidden'},
    slide_49:{hero_visibility:'hidden'},
  },
};

function isDemo001(lessonId:any):boolean{return String(lessonId||'demo_001')==='demo_001'}

export function lessonAvatarConfig(lesson:any):any{
  if(!isDemo001(lesson?.lesson_id))return lesson;
  return {...lesson,...DEMO_001_LAYOUT.lesson,hero_layout:{...(lesson?.hero_layout||{}),...DEMO_001_LAYOUT.lesson.hero_layout,anchors:{...(lesson?.hero_layout?.anchors||{}),...DEMO_001_LAYOUT.lesson.hero_layout.anchors}}};
}

export function slideAvatarConfig(slide:any,lessonId:any):any{
  if(!slide||!isDemo001(lessonId))return slide;
  const configured=DEMO_001_LAYOUT.slides[String(slide.slide_id)]||{};
  return {...slide,...configured,...(configured.hero_visibility==='hidden'?{hero_anchor:'hidden',hero_placement:'hidden'}:{})};
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
  const canonical=String(metadata?.canonicalFacing||'UNKNOWN').toUpperCase() as SourceAvatarFacing;const saved=String(metadata?.facingDirection||'UNKNOWN').toUpperCase() as SourceAvatarFacing;
  const value=(['LEFT','RIGHT','FRONT'].includes(canonical)?canonical:saved) as SourceAvatarFacing;
  return ['LEFT','RIGHT','FRONT'].includes(value)?value:'UNKNOWN';
}

export function avatarScaleX(desired:AvatarFacing,source:SourceAvatarFacing='UNKNOWN'):number{
  if(desired==='front'||source==='FRONT')return 1;
  if(source==='LEFT')return desired==='left'?1:-1;
  if(source==='RIGHT')return desired==='right'?1:-1;
  return desired==='left'?-1:1;
}

export type AvatarRenderTrace={sourceFacing:SourceAvatarFacing;desiredFacing:AvatarFacing;appliedFlip:boolean;displayedFacing:SourceAvatarFacing;confirmed:boolean;analysisVersion:string};
export function avatarRenderTrace(metadata:any,desiredFacing:AvatarFacing):AvatarRenderTrace{
  const sourceFacing=sourceAvatarFacing(metadata);const scaleX=avatarScaleX(desiredFacing,sourceFacing);let displayedFacing=sourceFacing;
  if(scaleX<0&&sourceFacing==='LEFT')displayedFacing='RIGHT';else if(scaleX<0&&sourceFacing==='RIGHT')displayedFacing='LEFT';
  return {sourceFacing,desiredFacing,appliedFlip:scaleX<0,displayedFacing,confirmed:metadata?.userConfirmed===true,analysisVersion:String(metadata?.analysisVersion||'legacy')};
}

export function visibleCharacterBox(metadata:any):[number,number,number,number]{
  const raw=metadata?.characterBoundingBox;const fallback:[number,number,number,number]=[0,0,1,1];
  if(!Array.isArray(raw)||raw.length!==4)return fallback;
  const left=Number(raw[0]);const top=Number(raw[1]);const width=Number(raw[2]);const height=Number(raw[3]);
  if(![left,top,width,height].every(Number.isFinite)||width<=0||height<=0)return fallback;
  const safeLeft=Math.max(0,Math.min(.99,left));const safeTop=Math.max(0,Math.min(.99,top));
  return [safeLeft,safeTop,Math.min(1-safeLeft,width),Math.min(1-safeTop,height)];
}

export function visibleCharacterAspect(metadata:any,fallback=.78):number{
  if(!metadata||typeof metadata!=='object')return Math.max(.18,Math.min(4,fallback));
  const direct=Number(metadata?.visibleAspectRatio);if(Number.isFinite(direct)&&direct>0)return Math.max(.18,Math.min(4,direct));
  const box=visibleCharacterBox(metadata);const width=Number(metadata?.sourceWidth);const height=Number(metadata?.sourceHeight);
  if(Number.isFinite(width)&&Number.isFinite(height)&&width>0&&height>0)return Math.max(.18,Math.min(4,(width*box[2])/(height*box[3])));
  return Math.max(.18,Math.min(4,box[2]/Math.max(.01,box[3])||fallback));
}

export function avatarGroundRatio(metadata:any):number{
  const box=visibleCharacterBox(metadata);const anchor=metadata?.feetAnchor||metadata?.groundAnchor;const y=Array.isArray(anchor)&&anchor.length===2?Number(anchor[1]):box[1]+box[3];
  return Math.max(.65,Math.min(1.15,(y-box[1])/Math.max(.01,box[3])));
}

export function avatarCanvasStyle(metadata:any):any{
  const box=visibleCharacterBox(metadata);const ground=avatarGroundRatio(metadata);const groundShift=(1-ground)*100;
  return {position:'absolute',left:`${-(box[0]/box[2])*100}%`,top:`${-(box[1]/box[3])*100+groundShift}%`,width:`${100/box[2]}%`,height:`${100/box[3]}%`};
}

export type AvatarImageFrame={left:number;top:number;width:number;height:number;visibleWidth:number;visibleHeight:number;sourceAspect:number};

/**
 * Converts confirmed transparent-padding geometry into one proportional image
 * frame.  Width and height always derive from the same scale, so wide/tall
 * child drawings retain their silhouette rather than being stretched to a
 * scene rectangle.
 */
export function avatarImageFrame(metadata:any,containerWidth:number,containerHeight:number):AvatarImageFrame{
  const boundsWidth=Math.max(0,Number(containerWidth)||0);const boundsHeight=Math.max(0,Number(containerHeight)||0);const box=visibleCharacterBox(metadata);
  const sourceWidth=Number(metadata?.sourceWidth);const sourceHeight=Number(metadata?.sourceHeight);
  const sourceAspect=Number.isFinite(sourceWidth)&&Number.isFinite(sourceHeight)&&sourceWidth>0&&sourceHeight>0
    ?Math.max(.18,Math.min(6,sourceWidth/sourceHeight))
    :Math.max(.18,Math.min(6,visibleCharacterAspect(metadata)*box[3]/Math.max(.01,box[2])));
  const visibleAspect=Math.max(.18,Math.min(6,sourceAspect*box[2]/Math.max(.01,box[3])));
  if(boundsWidth<=0||boundsHeight<=0)return {left:0,top:0,width:0,height:0,visibleWidth:0,visibleHeight:0,sourceAspect};
  const visibleWidth=Math.min(boundsWidth,boundsHeight*visibleAspect);const visibleHeight=visibleWidth/visibleAspect;
  const width=visibleWidth/Math.max(.01,box[2]);const height=width/sourceAspect;
  return {left:(boundsWidth-visibleWidth)/2-box[0]*width,top:boundsHeight-visibleHeight-box[1]*height,width,height,visibleWidth,visibleHeight,sourceAspect};
}
