export const RUNTIME_STAGES = [
  'ENTER',
  'AI_SPEAKING',
  'WAITING_INTERACTION',
  'WAITING_VOICE',
  'PROCESSING',
  'FEEDBACK',
  'RETRY_OR_COMPLETE',
  'NEXT',
] as const;

export type RuntimeStage = typeof RUNTIME_STAGES[number];
export type PromptPhase = 'initial'|'retry';

export function requiresSelection(slide:any):boolean{
  return Array.isArray(slide?.selection_options)||slide?.type==='card_selector'||slide?.type==='animal_compare'||slide?.type==='mood_choice'||slide?.interactive_task==='suitcase'||slide?.interaction_kind==='gift_selector'||slide?.slide_id==='slide_45';
}

export function requiresVoice(slide:any):boolean{
  return ['required_voice','optional_voice'].includes(String(slide?.answer_mode||''))||slide?.type==='card_selector'||slide?.type==='animal_compare';
}

export function stageAfterTutorSpeech(slide:any,hasSelection=false):RuntimeStage{
  if(requiresSelection(slide)&&!hasSelection)return 'WAITING_INTERACTION';
  if(requiresVoice(slide))return 'WAITING_VOICE';
  return 'NEXT';
}

export function recordEnabled(stage:RuntimeStage,slide:any,hasSelection=false):boolean{
  return stage==='WAITING_VOICE'&&requiresVoice(slide)&&(!requiresSelection(slide)||hasSelection);
}

export function nextEnabled(stage:RuntimeStage):boolean{return stage==='NEXT'}

export function advanceAfterAssessment(response:{accepted?:boolean;advance_allowed?:boolean;needs_retry?:boolean}):'COMPLETE'|'RETRY'{
  return response.accepted||response.advance_allowed?'COMPLETE':'RETRY';
}

export function complexitySupport(difficulty=0.15):string{
  if(difficulty<0.25)return 'Можно ответить одним словом или короткой фразой.';
  if(difficulty<0.5)return 'Ответь короткой фразой или одним предложением.';
  if(difficulty<0.72)return 'Ответь предложением и добавь одну деталь.';
  return 'Расскажи подробнее и добавь пример или объяснение.';
}

export function runtimePrompt(slide:any,languageLevel='PRE_A1',difficulty=0.15,phase:PromptPhase='initial'):string{
  const authored=String(slide?.bot_says_target||slide?.question||'').trim();
  const simplified=String(slide?.simplified_text||'').trim();
  // A low-level profile may add support, but must never replace the authored
  // opening/question with an example answer (the old blank-screen defect).
  const base=phase==='retry'&&simplified?simplified:authored;
  if(!base)return '';
  if(phase==='initial'&&slide?.adaptive&&['PRE_A1','A1'].includes(String(languageLevel).toUpperCase())){
    return `${base} ${complexitySupport(difficulty)}`.trim();
  }
  return base;
}

export type CardQuestion={id:string;text:string};

export function cardQuestions(slide:any,cardId:string):CardQuestion[]{
  const raw=slide?.card_question_sets?.[cardId];
  if(!Array.isArray(raw))return [];
  return raw.map((item:any,index:number)=>({id:String(item?.id||`${cardId}${index+1}`),text:String(item?.text||'')})).filter((item:CardQuestion)=>item.text);
}

export function cardVoiceKey(slideId:string,cardId:string,question:CardQuestion):string{
  return `${slideId}:${cardId}:${question.id}`;
}

export function nextCardQuestion(slide:any,cardId:string,currentIndex:number):{question?:CardQuestion;index:number;done:boolean}{
  const questions=cardQuestions(slide,cardId);const index=currentIndex+1;
  return index<questions.length?{question:questions[index],index,done:false}:{index:questions.length,done:true};
}

export type LayoutPolicy={
  landscape:boolean;
  compact:boolean;
  visualFlex:number;
  controlFlex:number;
  contentPadding:number;
  bottomPadding:number;
  controlsPinned:true;
};

export function lessonLayoutPolicy(width:number,height:number,bottomInset=0):LayoutPolicy{
  const landscape=width>height;
  const compact=Math.min(width,height)<390||height<650;
  return {
    landscape,
    compact,
    visualFlex:landscape?1.35:1,
    controlFlex:landscape?1:0,
    contentPadding:compact?10:16,
    bottomPadding:Math.max(bottomInset,10),
    controlsPinned:true,
  };
}

export function heroBox(slide:any,lesson:any):number[]|null{
  if(Array.isArray(slide?.hero_box)&&slide.hero_box.length===4)return slide.hero_box.map(Number);
  if(slide?.hero_placement==='left_of_mila')return [0.04,0.35,0.24,0.61];
  if((slide?.hero_placement||lesson?.default_hero_placement)==='hidden')return null;
  return null;
}
