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

export function nextEnabled(stage:RuntimeStage):boolean{return stage==='COMPLETE'}

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

export type CardQuestion={id:string;text:string};

export function cardQuestions(slide:any,cardId:string):CardQuestion[]{
  const raw=slide?.card_question_sets?.[cardId];
  if(!Array.isArray(raw))return [];
  return raw.map((item:any,index:number)=>({id:String(item?.id||`${cardId}${index+1}`),text:String(item?.text||'')})).filter((item:CardQuestion)=>item.text);
}

export function cardVoiceKey(slideId:string,cardId:string,question:CardQuestion):string{return `${slideId}:${cardId}:${question.id}`}

export function nextCardQuestion(slide:any,cardId:string,currentIndex:number):{question?:CardQuestion;index:number;done:boolean}{
  const questions=cardQuestions(slide,cardId);const index=currentIndex+1;
  return index<questions.length?{question:questions[index],index,done:false}:{index:questions.length,done:true};
}

export type LayoutPolicy={landscape:boolean;compact:boolean;visualFlex:number;controlFlex:number;contentPadding:number;bottomPadding:number;visualMaxHeight:number;controlsPinned:true};

export function lessonLayoutPolicy(width:number,height:number,bottomInset=0):LayoutPolicy{
  const landscape=width>height;const compact=Math.min(width,height)<390||height<700;
  const headerReserve=compact?58:72;const controlReserve=compact?238:276;
  return {landscape,compact,visualFlex:landscape?1.35:1,controlFlex:landscape?1:0,contentPadding:compact?8:14,bottomPadding:Math.max(bottomInset,8),visualMaxHeight:landscape?Math.max(220,height-headerReserve):Math.max(150,height-headerReserve-controlReserve-bottomInset),controlsPinned:true};
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
  for(const value of slide?.content_boxes||[]){const box=tuple(value);if(box)boxes.push(box)}
  for(const option of slide?.selection_options||[]){const box=tuple(option?.rect);if(box)boxes.push(box)}
  for(const key of ['character_box','question_card_box','prompt_box']){const box=tuple(slide?.[key]);if(box)boxes.push(box)}
  return boxes;
}

const DEFAULT_ANCHORS:Record<string,RectTuple>={left:[0.02,0.34,0.22,0.62],right:[0.76,0.34,0.22,0.62],bottom_left:[0.02,0.56,0.2,0.42],bottom_right:[0.78,0.56,0.2,0.42],left_of_mila:[0.04,0.35,0.24,0.61]};

function anchorBox(anchor:string,slide:any,lesson:any):RectTuple|null{
  const authored=tuple(slide?.hero_anchor_boxes?.[anchor]||lesson?.hero_layout?.anchors?.[anchor]);return authored||DEFAULT_ANCHORS[anchor]||null;
}

export function heroBox(slide:any,lesson:any):number[]|null{
  const placement=String(slide?.hero_anchor||slide?.hero_placement||lesson?.default_hero_placement||'hidden');if(placement==='hidden')return null;
  const preferred=tuple(slide?.hero_box)||anchorBox(placement,slide,lesson);
  const fallbacks=Array.from(new Set([...(slide?.hero_fallback_anchors||[]),...(lesson?.hero_layout?.fallback_order||[])]));
  const candidates=[preferred,...fallbacks.map(value=>anchorBox(String(value),slide,lesson))].filter(Boolean) as RectTuple[];
  const forbidden=slideContentBoxes(slide);return candidates.find(candidate=>!forbidden.some(box=>rectanglesOverlap(candidate,box)))||null;
}

export type PixelRect={x:number;y:number;width:number;height:number};
export function dropInsideTarget(pageX:number,pageY:number,target:PixelRect,padding=0):boolean{
  return pageX>=target.x-padding&&pageX<=target.x+target.width+padding&&pageY>=target.y-padding&&pageY<=target.y+target.height+padding;
}
