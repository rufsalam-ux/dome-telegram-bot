import {visibleCharacterAspect} from './avatarRuntime.ts';

export const AVATAR_PERCEPTUAL_SCALE=1.12;

export const RUNTIME_STAGES = [
  'ENTER', 'AI_SPEAKING', 'WAITING_ACTION', 'WAITING_VOICE', 'PROCESSING',
  'FEEDBACK', 'FOLLOW_UP', 'RETRY', 'COMPLETE',
] as const;

export type RuntimeStage = typeof RUNTIME_STAGES[number];
export type PromptPhase = 'initial'|'retry';
export type RectTuple = [number,number,number,number];
export type NextPolicy={requiredForMovie?:boolean;recoveryAvailable?:boolean;hasValidRecording?:boolean};

export const LESSON_OPERATION_TIMEOUT_MS=18_000;

export class LessonRuntimeTimeoutError extends Error{
  readonly operation:string;readonly timeoutMs:number;
  constructor(operation:string,timeoutMs:number){super(`${operation} timed out after ${timeoutMs}ms`);this.name='LessonRuntimeTimeoutError';this.operation=operation;this.timeoutMs=timeoutMs}
}

export function withLessonTimeout<T>(operation:Promise<T>,label:string,timeoutMs=LESSON_OPERATION_TIMEOUT_MS):Promise<T>{
  let timer:ReturnType<typeof setTimeout>|undefined;
  const timeout=new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new LessonRuntimeTimeoutError(label,timeoutMs)),timeoutMs)});
  return Promise.race([operation,timeout]).finally(()=>{if(timer)clearTimeout(timer)}) as Promise<T>;
}

export function requiresSelection(slide:any):boolean{
  const type=String(slide?.type||'');
  return Array.isArray(slide?.selection_options)||Array.isArray(slide?.riddle_options)||Array.isArray(slide?.options)||['choice','multiple_choice','tap_select','multi_select','listen_choose','odd_one_out','fill_gap','drag_drop','matching','memory','puzzle','ordering','sequence'].includes(type)||type==='card_selector'||type==='animal_compare'||type==='mood_choice'||slide?.interactive_task==='suitcase'||slide?.interaction_kind==='gift_selector';
}

export function requiresVoice(slide:any):boolean{
  return ['required_voice','optional_voice'].includes(String(slide?.answer_mode||''))||['voice_answer','required_movie_phrase','repeat','repeat_phrase','speak','dialogue','open_dialogue','roleplay','retell','continue_story','read_aloud','echo_reading','shared_reading','read_roles'].includes(String(slide?.type||''))||slide?.type==='card_selector'||slide?.type==='animal_compare';
}

export type VoiceRuntimeItem={id:string;labelTarget:string;labelNative:string};
export type VoiceRuntimeContext={
  task_type:string;
  selection_policy:string;
  target_language:string;
  interface_language:string;
  visible_items:string[];
  selected_items:string[];
  removed_items:string[];
};

export function buildVoiceRuntimeContext(slide:any,items:VoiceRuntimeItem[],selectedIds:string[],removedIds:string[],targetLanguage:string,interfaceLanguage:string):VoiceRuntimeContext{
  const visible=new Set(items.map(item=>String(item.id)));
  const selected=Array.from(new Set(selectedIds.map(String))).filter(id=>visible.has(id));
  const selectedSet=new Set(selected);
  return {
    task_type:String(slide?.interactive_task||slide?.interaction_kind||slide?.type||'voice'),
    selection_policy:String(slide?.selection_policy||(slide?.interactive_task==='suitcase'?'child_choice':'authored_choice')),
    target_language:String(targetLanguage||''),
    interface_language:String(interfaceLanguage||''),
    visible_items:[...visible],
    selected_items:selected,
    removed_items:Array.from(new Set(removedIds.map(String))).filter(id=>visible.has(id)&&!selectedSet.has(id)),
  };
}

export function childIdeaPrompt(itemLabel:string,taskType:string):string{
  const label=String(itemLabel||'').trim();
  if(String(taskType)==='animal_compare')return label?`Что ты хочешь сказать про ${label}?`:'Что ты хочешь сказать про это животное?';
  if(String(taskType)==='suitcase')return label?`Ты выбрал ${label}. Что ты хочешь сказать про свой выбор?`:'Что ты хочешь сказать про свой выбор?';
  return label?`Что ты хочешь сказать про ${label}?`:'Что ты хочешь сказать?';
}

export function stageAfterTutorSpeech(slide:any,hasSelection=false):RuntimeStage{
  if(requiresSelection(slide)&&!hasSelection)return 'WAITING_ACTION';
  if(requiresVoice(slide))return 'WAITING_VOICE';
  return 'COMPLETE';
}

export function recordEnabled(stage:RuntimeStage,slide:any,hasSelection=false):boolean{
  return stage==='WAITING_VOICE'&&requiresVoice(slide)&&(!requiresSelection(slide)||hasSelection);
}

export function answerEnabled(stage:RuntimeStage,slide:any,hasSelection=false,busy=false,recording=false):boolean{
  if(busy||recording||!requiresVoice(slide)||requiresSelection(slide)&&!hasSelection)return false;
  // FEEDBACK/RETRY/FOLLOW_UP are accepted as recoverable post-TTS states. This
  // prevents a stale non-playing stage from disabling Answer forever.
  // COMPLETE means the authored requirement is satisfied, not that the child
  // must stop talking. Optional conversation remains available while Next is
  // also active; required movie tasks still use nextEnabled's recording gate.
  return ['WAITING_VOICE','FEEDBACK','RETRY','FOLLOW_UP','COMPLETE'].includes(stage);
}

export type TutorAudioStatus={playing?:boolean;isBuffering?:boolean;isLoaded?:boolean;didJustFinish?:boolean;currentTime?:number;duration?:number;error?:string|null};
export type TutorAudioTransition={stage:RuntimeStage;sawPlayback:boolean;finished:boolean};

export function tutorAudioTransition(stage:RuntimeStage,status:TutorAudioStatus,sawPlayback:boolean,after:RuntimeStage):TutorAudioTransition{
  if(stage!=='AI_SPEAKING')return {stage,sawPlayback,finished:false};
  const observed=sawPlayback||Boolean(status.playing)||Number(status.currentTime||0)>0||Boolean(status.didJustFinish);
  const duration=Number(status.duration||0);const current=Number(status.currentTime||0);
  const reachedEnd=observed&&duration>0&&current>=Math.max(0,duration-.12)&&!status.playing&&!status.isBuffering;
  const finished=Boolean(status.didJustFinish)||reachedEnd;
  return {stage:finished?after:stage,sawPlayback:finished?false:observed,finished};
}

export function tutorAudioWatchdogStage(stage:RuntimeStage,status:TutorAudioStatus,after:RuntimeStage,hardDeadline=false,sawPlayback=false):RuntimeStage{
  const finished=Boolean(status.didJustFinish)||(hardDeadline&&sawPlayback&&!status.playing&&!status.isBuffering);
  return stage==='AI_SPEAKING'&&finished?after:stage;
}

export type RecordingGateState={speechStarted:boolean;silenceStartedAt:number|null;stopReason:'SPEECH_COMPLETE'|'SAFETY_LIMIT'|null};
export function recordingGate(previous:RecordingGateState,durationMillis:number,metering:number|undefined,nowMillis:number,hardLimitMillis=25_000,silenceMillis=1_250):RecordingGateState{
  if(durationMillis>=hardLimitMillis)return {...previous,stopReason:'SAFETY_LIMIT'};
  if(!Number.isFinite(metering as number))return {...previous,stopReason:null};
  const speech=Number(metering)>-42;
  if(speech)return {speechStarted:true,silenceStartedAt:null,stopReason:null};
  if(!previous.speechStarted)return {...previous,stopReason:null};
  const silenceStartedAt=previous.silenceStartedAt??nowMillis;
  return {speechStarted:true,silenceStartedAt,stopReason:durationMillis>=900&&nowMillis-silenceStartedAt>=silenceMillis?'SPEECH_COMPLETE':null};
}

export function isRequiredForMovie(slide:any):boolean{
  if(slide?.voice_after_action_optional===true)return false;
  return slide?.requiredForMovie===true||slide?.required_for_movie===true||Boolean(slide?.required_phrase_id&&slide?.allow_skip===false);
}

export function nextEnabled(stage:RuntimeStage,visualReady=true,policy:NextPolicy={requiredForMovie:true}):boolean{
  if(!visualReady)return false;
  if(policy.requiredForMovie!==true)return true;
  return stage==='COMPLETE'||policy.hasValidRecording===true;
}

export type ChildSafeOperation='lesson'|'recording'|'answer'|'interaction'|'progress'|'completion';
export function childSafeRuntimeMessage(operation:ChildSafeOperation):string{
  if(operation==='recording')return 'Не получилось сохранить запись. Нажми на микрофон и попробуй ещё раз.';
  if(operation==='answer')return 'Ответ пока не обработался. Попробуй ещё раз — твой прогресс сохранён.';
  if(operation==='interaction')return 'Не получилось сохранить выбор. Попробуй ещё раз.';
  if(operation==='progress')return 'Прогресс сохранится при следующем действии. Можно продолжать.';
  if(operation==='completion')return 'Не получилось завершить урок. Проверь интернет и попробуй ещё раз.';
  return 'Урок пока не открылся. Проверь интернет и попробуй ещё раз.';
}

export function recoveryStageAfterFailure(slide:any,hasSelection=false):RuntimeStage{
  if(requiresSelection(slide)&&!hasSelection)return 'WAITING_ACTION';
  if(requiresVoice(slide))return 'WAITING_VOICE';
  return 'COMPLETE';
}

export type ProgressiveHint={step:'REPHRASE'|'EXAMPLE'|'STARTER'|'CHOICES';prompt:string};

function sentenceStarter(value:string):string{
  const words=String(value||'').replace(/[.!?]+$/,'').trim().split(/\s+/).filter(Boolean);
  return words.slice(0,Math.min(3,Math.max(1,words.length-1))).join(' ')+(words.length>1?'…':'');
}

export function progressiveHint(slide:any,attempt:number):ProgressiveHint{
  const question=String(slide?.task_goal||slide?.question||slide?.bot_says_target||'Попробуй ещё раз.').trim();
  const example=String(slide?.model_examples?.[0]||slide?.simplified_text||slide?.model_answer_target||question).trim();
  const labels=(slide?.selection_options||slide?.riddle_options||[]).map((item:any)=>String(item?.label||item?.answer_value_ru||item?.id||'').trim()).filter(Boolean);
  const step=Math.max(1,Number(attempt)||1);
  if(step===1)return {step:'REPHRASE',prompt:question};
  if(step===2)return {step:'EXAMPLE',prompt:`Можно сказать: ${example}`};
  if(step===3)return {step:'STARTER',prompt:`Начни так: ${sentenceStarter(example)}`};
  return {step:'CHOICES',prompt:labels.length?`Выбери: ${labels.slice(0,4).join(' или ')}.`:`Попробуй ещё раз. Можно сказать: ${example}`};
}

export function adaptiveModelPhrase(slide:any,languageLevel='PRE_A1',difficulty=.15):string{
  const simple=String(slide?.model_examples?.[0]||slide?.simplified_text||slide?.model_answer_target||slide?.task_goal||slide?.question||'').trim();
  const richer=String(slide?.richer_model_text||slide?.model_answer_richer||'').trim();
  return richer&&String(languageLevel).toUpperCase()!=='PRE_A1'&&difficulty>=.45?richer:simple;
}

export function advanceAfterAssessment(response:{accepted?:boolean;advance_allowed?:boolean;needs_retry?:boolean;tutor_turn?:{follow_up_target?:string}}):'FOLLOW_UP'|'COMPLETE'|'RETRY'{
  if(response.accepted&&String(response.tutor_turn?.follow_up_target||'').trim())return 'FOLLOW_UP';
  return response.accepted||response.advance_allowed?'COMPLETE':'RETRY';
}

export function hasCorrectiveFeedback(response:any):boolean{
  const turn=response?.tutor_turn||{};
  return response?.needs_retry===true||Boolean(turn.model_answer_target||turn.correction_target||response?.correction_target)&&response?.accepted!==true||String(response?.voice_feedback_state||'').toUpperCase()==='PARTIALLY_CORRECT';
}

export function complexitySupport(difficulty=0.15):string{
  if(difficulty<0.25)return 'Можно ответить одним словом.';
  if(difficulty<0.5)return 'Ответь короткой фразой.';
  if(difficulty<0.72)return 'Ответь одним предложением.';
  return 'Добавь одну короткую деталь.';
}

export function runtimePrompt(slide:any,_languageLevel='PRE_A1',_difficulty=0.15,phase:PromptPhase='initial'):string{
  const authored=String(slide?.task_goal||slide?.bot_says_target||slide?.question||'').trim();
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
  for(const key of ['content_boxes','protected_zones','protected_character_boxes','face_boxes','key_label_boxes','question_card_boxes'])for(const value of slide?.[key]||[]){const box=tuple(value);if(box)boxes.push(box)}
  for(const option of slide?.selection_options||[]){const box=tuple(option?.rect);if(box)boxes.push(box)}
  for(const key of ['character_box','question_card_box','prompt_box']){const box=tuple(slide?.[key]);if(box)boxes.push(box)}
  return boxes;
}

const DEFAULT_ANCHORS:Record<string,RectTuple>={left:[0.01,0.27,0.48,0.69],right:[0.51,0.27,0.48,0.69],bottom_left:[0.01,0.42,0.48,0.54],bottom_right:[0.51,0.42,0.48,0.54],left_of_lyosha:[0.005,0.32,0.397,0.52],left_of_mila:[0.01,0.40,0.548,0.55]};

function anchorBox(anchor:string,slide:any,lesson:any):RectTuple|null{
  const authored=tuple(slide?.hero_anchor_boxes?.[anchor]||lesson?.hero_layout?.anchors?.[anchor]);return authored||DEFAULT_ANCHORS[anchor]||null;
}

export function computeHeroScale(containerWidth:number,containerHeight:number,authoredBox:number[],targetHeightRatio=.9,maxScale=3):number{
  const box=tuple(authoredBox);if(!box||containerWidth<=0||containerHeight<=0)return 1;
  const authoredPixelHeight=Math.max(1,box[3]*containerHeight);const targetPixelHeight=Math.min(containerHeight*.92,Math.max(containerHeight*targetHeightRatio,120));
  return Math.max(1,Math.min(maxScale,targetPixelHeight/authoredPixelHeight));
}

function fittedAtAnchor(box:RectTuple,height:number,placement:string,visibleAspect:number,containerWidth:number,containerHeight:number):RectTuple{
  const ratio=Math.max(.05,containerHeight/Math.max(1,containerWidth));const fittedHeight=Math.min(box[3],height,box[2]/Math.max(.01,visibleAspect*ratio));const width=Math.min(box[2],fittedHeight*visibleAspect*ratio);const bottom=Math.min(.99,box[1]+box[3]);
  const rightAligned=placement.startsWith('left_of_')||/(right)/.test(placement)||box[0]>.55;const x=rightAligned?box[0]+box[2]-width:box[0];
  return [Math.max(.005,Math.min(.995-width,x)),Math.max(.005,bottom-fittedHeight),width,fittedHeight];
}

export function heroBox(slide:any,lesson:any,containerWidth=360,containerHeight=203,metadata:any=null):number[]|null{
  const placement=String(slide?.hero_anchor||slide?.hero_placement||lesson?.default_hero_placement||'hidden');if(placement==='hidden')return null;
  const preferred=tuple(slide?.hero_box)||anchorBox(placement,slide,lesson);
  // An explicitly authored array is authoritative, including []. Partner-side
  // scenes must never jump across the partner merely because the other side is roomier.
  const fallbackSource=Array.isArray(slide?.hero_fallback_anchors)?slide.hero_fallback_anchors:(lesson?.hero_layout?.fallback_order||[]);
  const fallbacks=Array.from(new Set(fallbackSource));
  const anchors=[{box:preferred,placement},...fallbacks.map(value=>({box:anchorBox(String(value),slide,lesson),placement:String(value)}))].filter(value=>value.box) as {box:RectTuple;placement:string}[];
  const forbidden=slideContentBoxes(slide);const minimumRatio=Math.max(.28,Math.min(.72,Number(slide?.hero_min_visual_height_ratio||lesson?.hero_layout?.min_visual_height_ratio||.48)));const visibleAspect=visibleCharacterAspect(metadata);
  for(const anchor of anchors){
    const target=Math.min(anchor.box[3],Number(slide?.hero_target_visual_height_ratio||lesson?.hero_layout?.target_visual_height_ratio||.64)*AVATAR_PERCEPTUAL_SCALE);
    for(let height=target;height>=minimumRatio-.001;height-=.025){const candidate=fittedAtAnchor(anchor.box,height,anchor.placement,visibleAspect,containerWidth,containerHeight);if(candidate[3]>=minimumRatio-.001&&!forbidden.some(box=>rectanglesOverlap(candidate,box)))return candidate}
  }
  return null;
}

export function renderedPerceptualHeightRatio(child:RectTuple,partner:RectTuple,visibleAspect=1):number{
  return child[3]/Math.max(.01,partner[3])*Math.max(1,Number(visibleAspect)||1)**.32;
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
  void languageLevel;void difficulty;
  const compact=String(text||'').replace(/\s+/g,' ').trim();if(!compact)return '';
  const sentences=compact.match(/[^.!?]+[.!?]?/g)?.map(value=>value.trim()).filter(Boolean).slice(0,2)||[compact];const complete=sentences.join(' ');
  if(complete.length<=maxLength)return complete;
  const shortened=complete.slice(0,Math.max(1,maxLength-1));const boundary=shortened.lastIndexOf(' ');
  return `${shortened.slice(0,boundary>maxLength*0.55?boundary:shortened.length).trim()}…`;
}

export function completeHelperLanguage(authored:string,fallback:string,maxLength=220):string{
  const clean=(value:string)=>String(value||'').replace(/\s+/g,' ').trim();const primary=clean(authored);const translated=clean(fallback);
  const words=primary.split(/\s+/).filter(Boolean);const meaningful=primary.length>=14&&words.length>=3;
  return initialBilingualHint(meaningful?primary:(translated||primary),'PRE_A1',.15,maxLength);
}

export function interactionGuidance(slide:any):string{
  const explicit=String(slide?.interaction_prompt_native||slide?.tap_instruction_native||'').trim();if(explicit)return explicit;
  if(slide?.interaction_kind==='gift_selector')return 'Выбери подарок — нажми на одну из картинок выше.';
  if(slide?.interactive_task==='suitcase')return 'Перетащи нужный предмет в чемодан.';
  if(slide?.type==='card_selector'||slide?.interaction_kind==='card_question_sequence')return 'Выбери карточку — нажми на одну картинку выше.';
  if(slide?.type==='animal_compare')return 'Выбери животное — нажми на его картинку.';
  return 'Выбери ответ — нажми на подходящий предмет или картинку.';
}

export function droppedObjectTutorPrompt(label:string,currentPrompt:string):string{
  const name=String(label||'').trim();const prompt=String(currentPrompt||'').trim();
  return [name?`${name}!`:'',prompt].filter(Boolean).join(' ');
}

export function visualRequiredForSlide(slide:any):boolean{
  return Boolean(slide?.visual_required||slide?.interaction_kind==='gift_selector'||slide?.type==='card_selector'||slide?.type==='animal_compare'||Array.isArray(slide?.selection_options));
}
