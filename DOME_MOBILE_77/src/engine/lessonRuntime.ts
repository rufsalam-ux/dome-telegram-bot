export const RUNTIME_STAGES = [
  'ENTER', 'AI_SPEAKING', 'WAITING_ACTION', 'WAITING_VOICE', 'PROCESSING',
  'FEEDBACK', 'FOLLOW_UP', 'RETRY', 'COMPLETE',
] as const;

export type RuntimeStage = typeof RUNTIME_STAGES[number];
export type PromptPhase = 'initial'|'retry';
export type RectTuple = [number,number,number,number];

export function requiresSelection(slide:any):boolean{
  return Array.isArray(slide?.selection_options)||Array.isArray(slide?.riddle_options)||slide?.type==='card_selector'||slide?.type==='animal_compare'||slide?.type==='mood_choice'||slide?.interactive_task==='suitcase'||slide?.interaction_kind==='gift_selector';
}

export function requiresVoice(slide:any):boolean{
  return ['required_voice','optional_voice'].includes(String(slide?.answer_mode||''))||slide?.type==='card_selector'||slide?.type==='animal_compare';
}

export function stageAfterTutorSpeech(slide:any,hasSelection=false):RuntimeStage{
  if(requiresSelection(slide)&&!hasSelection)return 'WAITING_ACTION';
  if(requiresVoice(slide))return 'WAITING_VOICE';
  return 'COMPLETE';
}

export function recordEnabled(stage:RuntimeStage,slide:any,hasSelection=false):boolean{
  return stage==='WAITING_VOICE'&&requiresVoice(slide)&&(!requiresSelection(slide)||hasSelection);
}

export type TutorAudioStatus={playing?:boolean;isBuffering?:boolean;didJustFinish?:boolean};
export type TutorAudioTransition={stage:RuntimeStage;sawPlayback:boolean;finished:boolean};

export function tutorAudioTransition(stage:RuntimeStage,status:TutorAudioStatus,sawPlayback:boolean,after:RuntimeStage):TutorAudioTransition{
  if(stage!=='AI_SPEAKING')return {stage,sawPlayback,finished:false};
  const observed=sawPlayback||Boolean(status.playing)||Boolean(status.isBuffering);
  const finished=Boolean(status.didJustFinish)||(observed&&!status.playing&&!status.isBuffering);
  return {stage:finished?after:stage,sawPlayback:finished?false:observed,finished};
}

export function tutorAudioWatchdogStage(stage:RuntimeStage,status:TutorAudioStatus,after:RuntimeStage):RuntimeStage{
  return stage==='AI_SPEAKING'&&!status.playing&&!status.isBuffering?after:stage;
}

export function nextEnabled(stage:RuntimeStage,visualReady=true,voiceAfterActionOptional=false):boolean{return visualReady&&(stage==='COMPLETE'||(voiceAfterActionOptional&&stage==='WAITING_VOICE'))}

export function advanceAfterAssessment(response:{accepted?:boolean;advance_allowed?:boolean;needs_retry?:boolean;tutor_turn?:{follow_up_target?:string}}):'FOLLOW_UP'|'COMPLETE'|'RETRY'{
  if(response.accepted&&String(response.tutor_turn?.follow_up_target||'').trim())return 'FOLLOW_UP';
  return response.accepted||response.advance_allowed?'COMPLETE':'RETRY';
}

export function complexitySupport(difficulty=0.15):string{
  if(difficulty<0.25)return 'Можно ответить одним словом.';
  if(difficulty<0.5)return 'Ответь короткой фразой.';
  if(difficulty<0.72)return 'Ответь одним предложением.';
  return 'Добавь одну короткую деталь.';
}

export function runtimePrompt(slide:any,_languageLevel='PRE_A1',_difficulty=0.15,phase:PromptPhase='initial'):string{
  const authored=String(slide?.bot_says_target||slide?.question||'').trim();
  const simplified=String(slide?.simplified_text||'').trim();
  return phase==='retry'&&simplified?simplified:authored;
}

export type CardQuestion={id:string;text:string;preA1Text?:string};

export function cardQuestions(slide:any,cardId:string):CardQuestion[]{
  const raw=slide?.card_question_sets?.[cardId];
  if(!Array.isArray(raw))return [];
  return raw.map((item:any,index:number)=>({id:String(item?.id||`${cardId}${index+1}`),text:String(item?.text||''),preA1Text:item?.pre_a1_text?String(item.pre_a1_text):undefined})).filter((item:CardQuestion)=>item.text);
}

export function adaptiveCardQuestionText(question:CardQuestion|undefined,languageLevel='PRE_A1',difficulty=0.15):string{
  if(!question)return '';
  return (String(languageLevel||'').toUpperCase()==='PRE_A1'||difficulty<0.25)&&question.preA1Text?question.preA1Text:question.text;
}

export function cardSelectionAllowed(stage:RuntimeStage,selectedCardId=''):boolean{return stage==='WAITING_ACTION'&&!selectedCardId}

export function cardVoiceKey(slideId:string,cardId:string,question:CardQuestion):string{return `${slideId}:${cardId}:${question.id}`}

export function nextCardQuestion(slide:any,cardId:string,currentIndex:number):{question?:CardQuestion;index:number;done:boolean}{
  const questions=cardQuestions(slide,cardId);const index=currentIndex+1;
  return index<questions.length?{question:questions[index],index,done:false}:{index:questions.length,done:true};
}

export type LayoutPolicy={landscape:boolean;compact:boolean;visualFlex:number;controlFlex:number;contentPadding:number;bottomPadding:number;visualMinHeight:number;visualMaxHeight:number;controlsPinned:true};

export function lessonLayoutPolicy(width:number,height:number,bottomInset=0):LayoutPolicy{
  const landscape=width>height;const compact=Math.min(width,height)<390||height<700;
  const headerReserve=compact?50:66;const controlReserve=compact?238:288;
  const visualMaxHeight=landscape?Math.max(220,height-headerReserve):Math.max(150,height-headerReserve-controlReserve-bottomInset);
  return {landscape,compact,visualFlex:landscape?1.35:0,controlFlex:landscape?1:0,contentPadding:compact?8:14,bottomPadding:Math.max(bottomInset,8),visualMinHeight:Math.min(visualMaxHeight,compact?188:216),visualMaxHeight,controlsPinned:true};
}

function tuple(value:any):RectTuple|null{
  if(!Array.isArray(value)||value.length!==4)return null;
  const result=value.map(Number) as RectTuple;return result.every(Number.isFinite)?result:null;
}

export function rectanglesOverlap(a:RectTuple,b:RectTuple,margin=0.012):boolean{
  return a[0]<b[0]+b[2]+margin&&a[0]+a[2]+margin>b[0]&&a[1]<b[1]+b[3]+margin&&a[1]+a[3]+margin>b[1];
}

export function slideContentBoxes(slide:any):RectTuple[]{
  const boxes:RectTuple[]=[];
  for(const key of ['content_boxes','protected_zones','face_boxes','key_label_boxes','question_card_boxes'])for(const value of slide?.[key]||[]){const box=tuple(value);if(box)boxes.push(box)}
  for(const option of slide?.selection_options||[]){const box=tuple(option?.rect);if(box)boxes.push(box)}
  for(const key of ['character_box','question_card_box','prompt_box']){const box=tuple(slide?.[key]);if(box)boxes.push(box)}
  return boxes;
}

const DEFAULT_ANCHORS:Record<string,RectTuple>={left:[0.02,0.34,0.22,0.62],right:[0.76,0.34,0.22,0.62],bottom_left:[0.02,0.56,0.2,0.42],bottom_right:[0.78,0.56,0.2,0.42],left_of_mila:[0.04,0.35,0.24,0.61]};

function anchorBox(anchor:string,slide:any,lesson:any):RectTuple|null{
  const authored=tuple(slide?.hero_anchor_boxes?.[anchor]||lesson?.hero_layout?.anchors?.[anchor]);return authored||DEFAULT_ANCHORS[anchor]||null;
}

export function computeHeroScale(containerWidth:number,containerHeight:number,authoredBox:number[],targetHeightRatio=.9,maxScale=3):number{
  const box=tuple(authoredBox);if(!box||containerWidth<=0||containerHeight<=0)return 1;
  const authoredPixelHeight=Math.max(1,box[3]*containerHeight);const targetPixelHeight=Math.min(containerHeight*.92,Math.max(containerHeight*targetHeightRatio,120));
  return Math.max(1,Math.min(maxScale,targetPixelHeight/authoredPixelHeight));
}

function scaledAtAnchor(box:RectTuple,scale:number,placement:string):RectTuple{
  const width=Math.min(.96,box[2]*scale);const height=Math.min(.92,box[3]*scale);const bottom=Math.min(.98,box[1]+box[3]);
  const rightAligned=/(right)/.test(placement)||box[0]>.55;const leftEdge=placement==='left_of_mila'?.005:Math.max(.005,box[0]);const x=rightAligned?Math.min(.995-width,box[0]+box[2]-width):leftEdge;
  return [Math.max(.005,x),Math.max(.005,bottom-height),width,height];
}

export function heroBox(slide:any,lesson:any,containerWidth=360,containerHeight=203):number[]|null{
  const placement=String(slide?.hero_anchor||slide?.hero_placement||lesson?.default_hero_placement||'hidden');if(placement==='hidden')return null;
  const preferred=tuple(slide?.hero_box)||anchorBox(placement,slide,lesson);
  const fallbacks=Array.from(new Set([...(slide?.hero_fallback_anchors||[]),...(lesson?.hero_layout?.fallback_order||[])]));
  const anchors=[{box:preferred,placement},...fallbacks.map(value=>({box:anchorBox(String(value),slide,lesson),placement:String(value)}))].filter(value=>value.box) as {box:RectTuple;placement:string}[];
  const forbidden=slideContentBoxes(slide);const minimumRatio=Math.max(.44,Math.min(.72,Number(slide?.hero_min_visual_height_ratio||lesson?.hero_layout?.min_visual_height_ratio||.56)));
  for(const anchor of anchors){
    const preferredScale=computeHeroScale(containerWidth,containerHeight,anchor.box,Number(slide?.hero_target_visual_height_ratio||lesson?.hero_layout?.target_visual_height_ratio||.9));
    const minimumScale=Math.min(preferredScale,Math.max(1,minimumRatio/anchor.box[3]));
    for(let scale=preferredScale;scale>=1-.001;scale-=.04){const candidate=scaledAtAnchor(anchor.box,scale,anchor.placement);if(!forbidden.some(box=>rectanglesOverlap(candidate,box)))return candidate}
    const original=scaledAtAnchor(anchor.box,1,anchor.placement);if(!forbidden.some(box=>rectanglesOverlap(original,box)))return original;
  }
  return null;
}

export type PixelRect={x:number;y:number;width:number;height:number};
export type PixelPoint={x:number;y:number};
export function dropInsideTarget(pageX:number,pageY:number,target:PixelRect,padding=0):boolean{
  return validPixelRect(target)&&Number.isFinite(pageX)&&Number.isFinite(pageY)&&pageX>=target.x-padding&&pageX<=target.x+target.width+padding&&pageY>=target.y-padding&&pageY<=target.y+target.height+padding;
}

export function validPixelRect(rect:PixelRect|undefined|null):rect is PixelRect{
  return Boolean(rect&&Number.isFinite(rect.x)&&Number.isFinite(rect.y)&&Number.isFinite(rect.width)&&Number.isFinite(rect.height)&&rect.width>0&&rect.height>0);
}

export function movedPixelRect(origin:PixelRect|undefined|null,dx:number,dy:number):PixelRect|undefined{
  return validPixelRect(origin)&&Number.isFinite(dx)&&Number.isFinite(dy)?{...origin,x:origin.x+dx,y:origin.y+dy}:undefined;
}

export function pixelRectOverlapRatio(item:PixelRect|undefined|null,target:PixelRect|undefined|null):number{
  if(!validPixelRect(item)||!validPixelRect(target))return 0;
  const width=Math.max(0,Math.min(item.x+item.width,target.x+target.width)-Math.max(item.x,target.x));
  const height=Math.max(0,Math.min(item.y+item.height,target.y+target.height)-Math.max(item.y,target.y));
  return width*height/(item.width*item.height);
}

export function suitcaseDropAccepted(point:PixelPoint|undefined,item:PixelRect|undefined,target:PixelRect|undefined,padding=10,minItemOverlap=0.22):boolean{
  if(!validPixelRect(target))return false;
  return Boolean(point&&dropInsideTarget(point.x,point.y,target,padding))||pixelRectOverlapRatio(item,target)>=minItemOverlap;
}

export type SuitcaseDropOutcome='PACK'|'UNPACK'|'RETURN';
export function suitcaseDropOutcome(packed:boolean,inside:boolean):SuitcaseDropOutcome{
  if(!packed&&inside)return 'PACK';
  if(packed&&!inside)return 'UNPACK';
  return 'RETURN';
}

export function updatePackedItems(current:string[],itemId:string,outcome:SuitcaseDropOutcome):string[]{
  if(outcome==='PACK')return Array.from(new Set([...current,itemId]));
  if(outcome==='UNPACK')return current.filter(value=>value!==itemId);
  return current;
}

export function suitcaseTapFallbackAvailable(failedDrags:number,threshold=3):boolean{return failedDrags>=threshold}

export function initialBilingualHint(text:string,languageLevel='PRE_A1',difficulty=0.15,maxLength=120):string{
  if(String(languageLevel||'').toUpperCase()!=='PRE_A1'&&difficulty>=0.25)return '';
  const compact=String(text||'').replace(/\s+/g,' ').trim();if(!compact)return '';
  const first=compact.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim()||compact;
  if(first.length<=maxLength)return first;
  const shortened=first.slice(0,Math.max(1,maxLength-1));const boundary=shortened.lastIndexOf(' ');
  return `${shortened.slice(0,boundary>maxLength*0.55?boundary:shortened.length).trim()}…`;
}

export function droppedObjectTutorPrompt(label:string,currentPrompt:string):string{
  const name=String(label||'').trim();const prompt=String(currentPrompt||'').trim();
  return [name?`${name}!`:'',prompt].filter(Boolean).join(' ');
}

export function visualRequiredForSlide(slide:any):boolean{
  return Boolean(slide?.visual_required||slide?.interaction_kind==='gift_selector'||slide?.type==='card_selector'||slide?.type==='animal_compare'||Array.isArray(slide?.selection_options));
}
