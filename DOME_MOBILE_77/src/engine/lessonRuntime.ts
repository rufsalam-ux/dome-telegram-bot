import {visibleCharacterAspect} from './avatarRuntime.ts';
import {studiedLanguageForMobile} from '../data/languagePolicy.ts';

export const AVATAR_PERCEPTUAL_SCALE=1.12;

export const RUNTIME_STAGES = [
  'ENTER', 'AI_SPEAKING', 'WAITING_ACTION', 'WAITING_VOICE', 'PROCESSING',
  'FEEDBACK', 'FOLLOW_UP', 'RETRY', 'COMPLETE',
] as const;

export type RuntimeStage = typeof RUNTIME_STAGES[number];
export type PromptPhase = 'initial'|'retry';
export type RectTuple = [number,number,number,number];
export type NextPolicy={requiredForMovie?:boolean;recoveryAvailable?:boolean;hasValidRecording?:boolean;mode?:'always'|'after_action'|'after_answer'};
export type RuntimeLanguagePair={targetLanguage:string;explanationLanguage:string};
export type SourcedRuntimeText={text:string;sourceLanguage:string};
export type AdaptiveVisualAction={type:'highlight'|'animate'|'point'|'showChoice';objectIds:string[]};
export type AdaptiveResponse={
  spokenTextTarget:string;
  spokenTextSupport:string;
  supportNeeded:boolean;
  difficultyUsed:0|1|2|3|4;
  repairStep:number;
  visualAction:AdaptiveVisualAction|null;
  expectedResponseType:string;
  conversationContinues:boolean;
};

export type AdaptiveSkillSnapshot={
  comprehension?:number;
  vocabulary?:number;
  speaking?:number;
  grammar?:number;
  confidence?:number;
  derived_level?:number;
  meaningful_turns?:number;
};

export type AdaptivePromptPlan={
  text:string;
  sourceLanguage:string;
  difficulty:0|1|2|3|4;
  supportNeeded:boolean;
  objectIds:string[];
  expectedResponseType:'word'|'short_phrase'|'sentence'|'open_answer';
};

export type AnswerEvaluation = 'CONFIDENT_CORRECT' | 'PARTIAL_CORRECT' | 'INCORRECT' | 'SILENCE' | 'EXCELLENT' | 'CORRECT' | 'PARTIAL' | 'RETRY' | 'NONE';

export interface CurrentConversationTurn {
  currentSlideId: string;
  currentConversationTurnId: number;
  currentQuestion: string;
  currentQuestionNative: string;
  currentIntent: string;
  currentTargetObject: string;
  currentDifficultyLevel: number; // 0..4
  lastChildAnswer: string;
  currentExpectedAnswerType: string;
  targetLanguage: string;
  explanationLanguage: string;
}

export interface AdaptiveHintPair {
  hintTarget: string;
  hintNative: string;
}

export interface AdaptiveDialogueState extends CurrentConversationTurn {
  slideId: string;
  currentObject: string;
  currentLevel: number; // 0..4
  lastChildAnswer: string;
  answerEvaluation: AnswerEvaluation;
  nextQuestion: string;
  currentHint: string;
  awaitingChildAnswer: boolean;
}

/**
 * Remove all internal prompt variables, debug state, and system notes before
 * rendering text to a child.
 */
export function cleanChildFacingText(value: unknown): string {
  if (typeof value !== 'string') return '';
  let text = value.replace(/\r\n/g, '\n').trim();
  text = text.replace(/Current localized wording shown to the child:.*$/gmi, '');
  text = text.replace(/Current localized wording.*$/gmi, '');
  text = text.replace(/\[(?:DEBUG|SYSTEM|TUTOR|PROMPT|INTERNAL|SESSION)[^\]]*\]/gi, '');
  text = text.replace(/\{[a-zA-Z0-9_]+\}/g, '');
  text = text.replace(/\n\s*\n+/g, '\n').replace(/[ \t]+/g, ' ').trim();
  return text;
}

export function slideTargetObject(slide: any): string {
  if (!slide) return '';
  const explicit = slide.correct_choice_id || slide.target_object || slide.targetObject || slide.required_phrase_id || slide.animal_id;
  if (explicit) return String(explicit).trim();
  if (Array.isArray(slide.riddle_options) && slide.correct_choice_id) {
    return String(slide.correct_choice_id).trim();
  }
  const vmLabel = slide?.visual_metadata?.label || slide?.image_label;
  if (vmLabel) return String(vmLabel).trim();
  return '';
}

/** Validate the server decision against objects rendered in this exact turn. */
export function normalizeAdaptiveResponse(value:any,visibleIds:string[]):AdaptiveResponse{
  const visible=new Set((visibleIds||[]).map(String));
  const rawAction=value?.visualAction;
  const type=String(rawAction?.type||'');
  const requested:string[]=Array.isArray(rawAction?.objectIds)?rawAction.objectIds.map((item:unknown)=>String(item)):[];
  const safeIds:string[]=Array.from(new Set<string>(requested)).filter(id=>visible.has(id)).slice(0,2);
  const visualAction:AdaptiveVisualAction|null=(['highlight','animate','point','showChoice'].includes(type)&&safeIds.length===requested.length&&safeIds.length)
    ?{type:type as AdaptiveVisualAction['type'],objectIds:safeIds}
    :null;
  const rawDifficulty=Number(value?.difficultyUsed);
  const difficulty=Math.max(0,Math.min(4,Number.isFinite(rawDifficulty)?Math.trunc(rawDifficulty):0)) as 0|1|2|3|4;
  const supportNeeded=value?.supportNeeded===true&&String(value?.spokenTextSupport||'').trim().length>0;
  return {
    spokenTextTarget:cleanChildFacingText(value?.spokenTextTarget||''),
    spokenTextSupport:supportNeeded?cleanChildFacingText(value?.spokenTextSupport||''):'',
    supportNeeded,
    difficultyUsed:difficulty,
    repairStep:Math.max(0,Math.min(5,Math.trunc(Number(value?.repairStep)||0))),
    visualAction,
    expectedResponseType:String(value?.expectedResponseType||'voice'),
    conversationContinues:Boolean(value?.conversationContinues),
  };
}

function adaptiveLevel(profile:AdaptiveSkillSnapshot|undefined,languageLevel='PRE_A1',difficulty=.15,hintDepth=0):0|1|2|3|4{
  const explicit=Number(profile?.derived_level);
  const live=Math.floor(Math.max(0,Math.min(.99,Number(difficulty)||0))*5);
  const score=Number(profile?.meaningful_turns||0)>0
    ?live
    :Number.isFinite(explicit)?explicit:String(languageLevel||'').toUpperCase()==='PRE_A1'?live:Math.round(Math.max(0,Math.min(1,Number(difficulty)||0))*4);
  return Math.max(0,Math.min(4,Math.trunc(score)-Math.max(0,Math.trunc(hintDepth)))) as 0|1|2|3|4;
}

function adaptiveText(value:any):string{
  return cleanChildFacingText(value?.text??value??'');
}

function uniqueAdaptiveModels(slide:any,items:VoiceRuntimeItem[]):string[]{
  const targetId = slideTargetObject(slide);
  const matchedItem = targetId ? items.find(it => String(it.id).toLowerCase() === targetId.toLowerCase()) : undefined;
  const primaryItem = matchedItem || items[0];
  const firstLabel=adaptiveText(primaryItem?.labelTarget);
  const authoredModels=(Array.isArray(slide?.adaptive_models)?slide.adaptive_models:[]).map(adaptiveText);
  const regularModels=(slide?.target_language_options||slide?.model_examples||[]).map(adaptiveText);
  const candidates=[
    authoredModels[0]||firstLabel,
    authoredModels[1]||adaptiveText(slide?.simplified_text)||regularModels[0]||firstLabel,
    authoredModels[2]||regularModels[1]||adaptiveText(slide?.model_answer_target)||regularModels[0]||firstLabel,
    authoredModels[3]||adaptiveText(slide?.richer_model_text)||adaptiveText(slide?.model_answer_richer)||regularModels[2]||regularModels[1],
    authoredModels[4]||adaptiveText(slide?.task_goal)||adaptiveText(slide?.question)||adaptiveText(slide?.bot_says_target),
  ];
  const output:string[]=[];
  for(const value of candidates){if(value&&!output.includes(value))output.push(value)}
  return output;
}

/**
 * Build the next child-facing target-language prompt before rendering it.
 * Content supplies the language itself; the engine only selects a rung, so it
 * works for future lessons and never guesses morphology from an object ID.
 */
export function adaptivePromptPlan(
  slide:any,
  profile:AdaptiveSkillSnapshot|undefined,
  items:VoiceRuntimeItem[],
  languageLevel='PRE_A1',
  difficulty=.15,
  phase:'initial'|'hint'='initial',
  hintDepth=0,
  sourceLanguage=authoredTextLanguage(undefined,slide),
  targetLanguage='ru',
  supportLanguage='ru',
):AdaptivePromptPlan{
  const level=adaptiveLevel(profile,languageLevel,difficulty,phase==='hint'?hintDepth:0);
  const hasAuthoredAdaptiveModels = Array.isArray(slide?.adaptive_models) && slide.adaptive_models.length > 0;
  const models=uniqueAdaptiveModels(slide,items);
  const authored=adaptiveText(slide?.task_goal||slide?.bot_says_target||slide?.question);
  const model=models[Math.min(level,Math.max(0,models.length-1))]||models[0]||authored;
  const text=(phase==='initial'&&!hasAuthoredAdaptiveModels&&authored)?authored:(phase==='initial'&&level>=4&&authored?authored:model);
  const comprehension=Number(profile?.comprehension);
  const supportNeeded=normalizeRuntimeLanguage(targetLanguage)!==normalizeRuntimeLanguage(supportLanguage)
    &&(level===0||(Number.isFinite(comprehension)&&comprehension<30&&Number(difficulty)<.38));
  const targetId = slideTargetObject(slide);
  const matchedItem = targetId ? items.find(it => String(it.id).toLowerCase() === targetId.toLowerCase()) : undefined;
  const primaryItem = matchedItem || items[0];
  const objectIds=((phase==='hint'||level<=2)&&primaryItem?.id)?[String(primaryItem.id)]:[];
  const expectedResponseType=(['word','short_phrase','sentence','open_answer','open_answer'] as const)[level];
  return {text,sourceLanguage:normalizeRuntimeLanguage(sourceLanguage),difficulty:level,supportNeeded,objectIds,expectedResponseType};
}

export function buildNaturalReaction(
  transcript: string,
  accepted: boolean,
  semanticMatch = 1,
  currentObject = ''
): string {
  const clean = cleanChildFacingText(transcript).replace(/[.,!?;:]/g, ' ').replace(/\s+/g, ' ').trim();
  if (!clean) {
    return 'Хочешь, я подскажу? Попробуем вместе!';
  }
  if (!accepted) {
    return 'Попробуем ещё раз!';
  }
  const lower = clean.toLowerCase();
  if (lower.startsWith('это ')) {
    return `Да! ${clean}!`;
  }
  if (semanticMatch >= 0.9) {
    return `Да! Это ${clean}!`;
  }
  return `Отлично, ${clean}!`;
}

export function sanitizeTutorReaction(
  backendReaction: string | undefined,
  transcript: string,
  accepted: boolean,
  currentObject = ''
): string {
  const cleaned = cleanChildFacingText(backendReaction || '');
  const isGeneric = !cleaned || /^(это интересный ответ|интересный ответ|хорошо|молодец|здорово|отлично)[.!]?$/i.test(cleaned);
  if (isGeneric && transcript && accepted) {
    return buildNaturalReaction(transcript, accepted, 1, currentObject);
  }
  return cleaned || (accepted ? (transcript ? `Да, ${cleanChildFacingText(transcript)}!` : 'Отлично!') : 'Попробуй ещё раз.');
}

export function adaptiveQuestionForLevel(
  objectKey: string,
  level: number,
  slide?: any
): { questionTarget: string; questionNative: string; expectedAnswerModel: string } {
  const obj = (objectKey || slideTargetObject(slide) || '').toLowerCase();
  const isGiraffe = obj === 'giraffe' || obj.includes('жираф');
  const isBear = obj === 'polar_bear' || obj.includes('медвед');
  const isParrot = obj === 'parrot' || obj.includes('попуга');

  if (isGiraffe) {
    switch (Math.max(0, Math.min(4, level))) {
      case 0:
        return { questionTarget: 'Это жираф. Повтори: жираф.', questionNative: 'This is a giraffe. Repeat: giraffe.', expectedAnswerModel: 'Жираф.' };
      case 1:
        return { questionTarget: 'Кто это?', questionNative: 'Who is this?', expectedAnswerModel: 'Это жираф.' };
      case 2:
        return { questionTarget: 'Какой жираф?', questionNative: 'What is the giraffe like?', expectedAnswerModel: 'Жираф высокий.' };
      case 3:
        return { questionTarget: 'Что делает жираф?', questionNative: 'What is the giraffe doing?', expectedAnswerModel: 'Жираф стоит.' };
      case 4:
      default:
        return { questionTarget: 'Где живёт жираф?', questionNative: 'Where does the giraffe live?', expectedAnswerModel: 'Жираф живёт в Африке.' };
    }
  }

  if (isBear) {
    switch (Math.max(0, Math.min(4, level))) {
      case 0:
        return { questionTarget: 'Это белый медведь. Повтори: белый медведь.', questionNative: 'This is a polar bear. Repeat: polar bear.', expectedAnswerModel: 'Белый медведь.' };
      case 1:
        return { questionTarget: 'Кто это?', questionNative: 'Who is this?', expectedAnswerModel: 'Это белый медведь.' };
      case 2:
        return { questionTarget: 'Какой белый медведь?', questionNative: 'What is the white bear like?', expectedAnswerModel: 'Белый медведь большой.' };
      case 3:
        return { questionTarget: 'Что делает белый медведь?', questionNative: 'What is the white bear doing?', expectedAnswerModel: 'Медведь гуляет.' };
      case 4:
      default:
        return { questionTarget: 'Где живёт белый медведь?', questionNative: 'Where does the white bear live?', expectedAnswerModel: 'Белый медведь живёт на севере.' };
    }
  }

  if (isParrot) {
    switch (Math.max(0, Math.min(4, level))) {
      case 0:
        return { questionTarget: 'Это попугай. Повтори: попугай.', questionNative: 'This is a parrot. Repeat: parrot.', expectedAnswerModel: 'Попугай.' };
      case 1:
        return { questionTarget: 'Кто это?', questionNative: 'Who is this?', expectedAnswerModel: 'Это попугай.' };
      case 2:
        return { questionTarget: 'Какой попугай?', questionNative: 'What is the parrot like?', expectedAnswerModel: 'Попугай красивый.' };
      case 3:
        return { questionTarget: 'Что делает попугай?', questionNative: 'What is the parrot doing?', expectedAnswerModel: 'Попугай летает.' };
      case 4:
      default:
        return { questionTarget: 'Где живёт попугай?', questionNative: 'Where does the parrot live?', expectedAnswerModel: 'Попугай живёт в тёплом месте.' };
    }
  }

  const rawLabel = slide?.visual_metadata?.label || slide?.image_label || '';
  const cleanLabel = (rawLabel && !['объект', 'object', 'предмет', 'item', 'thing', 'undefined'].includes(rawLabel.toLowerCase())) ? rawLabel : '';
  const slideQuestion = cleanChildFacingText(slide?.question || slide?.task_goal || slide?.bot_says_target || '');
  const modelAnswer = cleanChildFacingText(slide?.simplified_text || (Array.isArray(slide?.model_examples) ? slide.model_examples[0] : '') || '');

  if (cleanLabel) {
    switch (Math.max(0, Math.min(4, level))) {
      case 0:
        return { questionTarget: `Это ${cleanLabel}. Повтори: ${cleanLabel}.`, questionNative: `This is ${cleanLabel}. Repeat: ${cleanLabel}.`, expectedAnswerModel: `${cleanLabel}.` };
      case 1:
        return { questionTarget: slideQuestion || 'Кто это или что это?', questionNative: 'What or who is this?', expectedAnswerModel: `Это ${cleanLabel}.` };
      case 2:
        return { questionTarget: `Какой ${cleanLabel}?`, questionNative: `What is the ${cleanLabel} like?`, expectedAnswerModel: `${cleanLabel} красивый.` };
      case 3:
        return { questionTarget: `Что делает ${cleanLabel}?`, questionNative: `What is the ${cleanLabel} doing?`, expectedAnswerModel: `${cleanLabel} стоит.` };
      case 4:
      default:
        return { questionTarget: slideQuestion || `Расскажи подробнее про ${cleanLabel}.`, questionNative: `Tell more about the ${cleanLabel}.`, expectedAnswerModel: `${cleanLabel} большой и интересный.` };
    }
  }

  switch (Math.max(0, Math.min(4, level))) {
    case 0:
      return {
        questionTarget: modelAnswer ? (modelAnswer.startsWith('Это ') || modelAnswer.startsWith('Я ') ? `Скажи: ${modelAnswer}` : `Повтори: ${modelAnswer}`) : (slideQuestion || 'Давай повторим вместе!'),
        questionNative: 'Repeat the answer model.',
        expectedAnswerModel: modelAnswer || 'Хорошо.'
      };
    case 1:
      return {
        questionTarget: slideQuestion || 'Что ты думаешь?',
        questionNative: 'What do you think?',
        expectedAnswerModel: modelAnswer || 'Мне это нравится.'
      };
    case 2:
      return {
        questionTarget: slideQuestion || 'Расскажи подробнее.',
        questionNative: 'Tell more details.',
        expectedAnswerModel: modelAnswer || 'Это очень интересно.'
      };
    case 3:
    case 4:
    default:
      return {
        questionTarget: slideQuestion || 'Расскажи подробнее, что ты думаешь.',
        questionNative: 'Tell more about what you think.',
        expectedAnswerModel: modelAnswer || 'Я думаю, это здорово.'
      };
  }
}

export function buildAdaptiveHintPair(
  currentQuestion: string,
  currentObject: string,
  currentLevel: number,
  slide: any,
  targetLang = 'ru',
  nativeLang = 'ru'
): AdaptiveHintPair {
  const q = cleanChildFacingText(currentQuestion).toLowerCase();
  const obj = (currentObject || slideTargetObject(slide) || '').toLowerCase();
  const lvl = Math.max(0, Math.min(4, Math.floor(Number(currentLevel) || 0)));

  // 1. Mood choice
  if (slide?.type === 'mood_choice' || q.includes('настроени')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: У меня отличное настроение!', hintNative: 'You can say: I am in a great mood!' };
      case 1:
        return { hintTarget: 'Весёлое или радостное?', hintNative: 'Cheerful or joyful?' };
      case 2:
        return { hintTarget: 'Скажи: У меня настроение...', hintNative: 'Say: My mood is...' };
      case 3:
      case 4:
      default:
        return { hintTarget: 'Подсказка: отличное, весёлое или спокойное', hintNative: 'Clue: great, cheerful, or calm' };
    }
  }

  // 1a. Greetings & feelings (e.g. slide_01)
  if (q.includes('чувствуешь') || q.includes('поздоровайся') || q.includes('как дела') || q.includes('привет')) {
    switch (lvl) {
      case 0: return { hintTarget: 'Можно сказать: Привет! У меня всё хорошо.', hintNative: 'You can say: Hello! I am doing well.' };
      case 1: return { hintTarget: 'Хорошо или отлично?', hintNative: 'Good or great?' };
      case 2: return { hintTarget: 'Скажи: У меня всё...', hintNative: 'Say: I am doing...' };
      default: return { hintTarget: 'Подсказка: отлично, хорошо, весело', hintNative: 'Clue: great, good, cheerful' };
    }
  }

  // 1b. Name (e.g. slide_07)
  if (q.includes('зовут') || q.includes('имя') || q.includes('как твоё имя')) {
    switch (lvl) {
      case 0: return { hintTarget: 'Можно сказать: Меня зовут...', hintNative: 'You can say: My name is...' };
      case 1: return { hintTarget: 'Назови своё имя.', hintNative: 'Say your name.' };
      case 2: return { hintTarget: 'Скажи: Меня зовут...', hintNative: 'Say: My name is...' };
      default: return { hintTarget: 'Подсказка: скажи своё имя', hintNative: 'Clue: say your name' };
    }
  }

  // 1c. Age and city (e.g. slide_08)
  if (q.includes('лет') || q.includes('возраст') || q.includes('живёшь') || q.includes('город')) {
    switch (lvl) {
      case 0: return { hintTarget: 'Можно сказать: Мне шесть лет.', hintNative: 'You can say: I am six years old.' };
      case 1: return { hintTarget: 'Сколько тебе лет или в каком городе живёшь?', hintNative: 'How old are you or which city do you live in?' };
      case 2: return { hintTarget: 'Скажи: Мне...', hintNative: 'Say: I am...' };
      default: return { hintTarget: 'Подсказка: назови свой возраст или город', hintNative: 'Clue: name your age or city' };
    }
  }

  // 1d. Lyosha clothing (e.g. slide_19)
  if (q.includes('лёш') || q.includes('одежд') || q.includes('жарко') || q.includes('куртк')) {
    switch (lvl) {
      case 0: return { hintTarget: 'Можно сказать: Почему ты тепло одет?', hintNative: 'You can say: Why are you dressed warmly?' };
      case 1: return { hintTarget: 'Тебе не жарко или почему ты в куртке?', hintNative: "Aren't you hot or why are you wearing a jacket?" };
      case 2: return { hintTarget: 'Скажи: Лёша, почему...', hintNative: 'Say: Lyosha, why...' };
      default: return { hintTarget: 'Подсказка: спроси, почему Лёше не жарко в куртке', hintNative: 'Clue: ask why Lyosha is wearing a warm jacket' };
    }
  }

  // 2. Card questions (e.g. from slide_09 card question sets)
  if (q.includes('завтрак') || (q.includes('люблю') && q.includes('утр'))) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Я люблю кашу и яблоки.', hintNative: 'You can say: I like porridge and apples.' };
      case 1:
        return { hintTarget: 'Кашу или блинчики?', hintNative: 'Porridge or pancakes?' };
      case 2:
        return { hintTarget: 'Скажи: На завтрак я люблю...', hintNative: 'Say: For breakfast I like...' };
      default:
        return { hintTarget: 'Подсказка: каша, блинчики, хлопья', hintNative: 'Clue: porridge, pancakes, cereal' };
    }
  }

  if (q.includes('на улице') || q.includes('не люблю, когда')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Не люблю, когда идёт дождь.', hintNative: "You can say: I don't like when it rains." };
      case 1:
        return { hintTarget: 'Дождь или холодный ветер?', hintNative: 'Rain or cold wind?' };
      case 2:
        return { hintTarget: 'Скажи: Не люблю, когда...', hintNative: "Say: I don't like when..." };
      default:
        return { hintTarget: 'Подсказка: дождь, слякоть или мороз', hintNative: 'Clue: rain, slush, or frost' };
    }
  }

  if (q.includes('летать') || q.includes('рыба') || q.includes('плавать')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Я хочу летать, как птица.', hintNative: 'You can say: I want to fly like a bird.' };
      case 1:
        return { hintTarget: 'Летать, как птица, или плавать, как рыба?', hintNative: 'Fly like a bird or swim like a fish?' };
      case 2:
        return { hintTarget: 'Скажи: Я бы хотел...', hintNative: 'Say: I would like to...' };
      default:
        return { hintTarget: 'Подсказка: летать высоко или плавать быстро', hintNative: 'Clue: fly high or swim fast' };
    }
  }

  if (q.includes('супергеро') || q.includes('суперспособност')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Моя суперспособность — летать.', hintNative: 'You can say: My superpower is flying.' };
      case 1:
        return { hintTarget: 'Летать или быть невидимым?', hintNative: 'Fly or be invisible?' };
      case 2:
        return { hintTarget: 'Скажи: Моя суперспособность...', hintNative: 'Say: My superpower is...' };
      default:
        return { hintTarget: 'Подсказка: летать, суперсила или невидимость', hintNative: 'Clue: fly, super strength, or invisibility' };
    }
  }

  if (q.includes('боюсь') || q.includes('страш')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Иногда я боюсь темноты.', hintNative: 'You can say: Sometimes I am afraid of the dark.' };
      case 1:
        return { hintTarget: 'Темноты или пауков?', hintNative: 'The dark or spiders?' };
      case 2:
        return { hintTarget: 'Скажи: Иногда я боюсь...', hintNative: 'Say: Sometimes I am afraid of...' };
      default:
        return { hintTarget: 'Подсказка: темнота, гроза или пауки', hintNative: 'Clue: dark, thunderstorm, or spiders' };
    }
  }

  if (q.includes('животное, которое') || q.includes('нравится животное')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Мне очень нравится собака.', hintNative: 'You can say: I really like dogs.' };
      case 1:
        return { hintTarget: 'Собака или кошка?', hintNative: 'A dog or a cat?' };
      case 2:
        return { hintTarget: 'Скажи: Мне очень нравится...', hintNative: 'Say: I really like...' };
      default:
        return { hintTarget: 'Подсказка: собака, кошка или дельфин', hintNative: 'Clue: dog, cat, or dolphin' };
    }
  }

  if (q.includes('игрушк')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Моя любимая игрушка — конструктор.', hintNative: 'You can say: My favorite toy is building blocks.' };
      case 1:
        return { hintTarget: 'Конструктор или машинка?', hintNative: 'Building blocks or a car?' };
      case 2:
        return { hintTarget: 'Скажи: Моя любимая игрушка...', hintNative: 'Say: My favorite toy is...' };
      default:
        return { hintTarget: 'Подсказка: конструктор, мишка или машинка', hintNative: 'Clue: blocks, bear, or car' };
    }
  }

  if (q.includes('дождь из еды')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Пусть идёт дождь из мороженого!', hintNative: 'You can say: Let it rain ice cream!' };
      case 1:
        return { hintTarget: 'Из мороженого или ягод?', hintNative: 'Ice cream or berries?' };
      case 2:
        return { hintTarget: 'Скажи: Я бы хотел дождь из...', hintNative: 'Say: I would like rain of...' };
      default:
        return { hintTarget: 'Подсказка: мороженое, ягоды или фрукты', hintNative: 'Clue: ice cream, berries, or fruit' };
    }
  }

  if (q.includes('друг')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Настоящий друг добрый и верный.', hintNative: 'You can say: A true friend is kind and loyal.' };
      case 1:
        return { hintTarget: 'Добрым или весёлым?', hintNative: 'Kind or funny?' };
      case 2:
        return { hintTarget: 'Скажи: Настоящий друг...', hintNative: 'Say: A true friend is...' };
      default:
        return { hintTarget: 'Подсказка: добрый, верный, весёлый', hintNative: 'Clue: kind, loyal, funny' };
    }
  }

  if (q.includes('грустить')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Дождик может заставить меня грустить.', hintNative: 'You can say: Rain can make me feel sad.' };
      case 1:
        return { hintTarget: 'Скука или плохая погода?', hintNative: 'Boredom or bad weather?' };
      case 2:
        return { hintTarget: 'Скажи: Меня может огорчить...', hintNative: 'Say: What makes me sad is...' };
      default:
        return { hintTarget: 'Подсказка: скука или плохая погода', hintNative: 'Clue: boredom or bad weather' };
    }
  }

  if (q.includes('три слова')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Весёлый, добрый и смелый.', hintNative: 'You can say: Cheerful, kind, and brave.' };
      case 1:
        return { hintTarget: 'Весёлый или смелый?', hintNative: 'Cheerful or brave?' };
      case 2:
        return { hintTarget: 'Скажи: Я весёлый, добрый...', hintNative: 'Say: I am cheerful, kind...' };
      default:
        return { hintTarget: 'Подсказка: весёлый, умный, добрый, смелый', hintNative: 'Clue: cheerful, smart, kind, brave' };
    }
  }

  if (q.includes('магазин')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: В моём магазине продавались бы книги и игрушки.', hintNative: 'You can say: Books and toys would be sold in my store.' };
      case 1:
        return { hintTarget: 'Игрушки или сладости?', hintNative: 'Toys or sweets?' };
      case 2:
        return { hintTarget: 'Скажи: В моём магазине...', hintNative: 'Say: In my store...' };
      default:
        return { hintTarget: 'Подсказка: книги, игрушки или сладости', hintNative: 'Clue: books, toys, or sweets' };
    }
  }

  if (q.includes('вкусная еда') || q.includes('вкусная')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Самая вкусная еда — это пицца.', hintNative: 'You can say: The most delicious food is pizza.' };
      case 1:
        return { hintTarget: 'Пицца или фрукты?', hintNative: 'Pizza or fruit?' };
      case 2:
        return { hintTarget: 'Скажи: Самая вкусная еда...', hintNative: 'Say: The most delicious food is...' };
      default:
        return { hintTarget: 'Подсказка: пицца, фрукты, блинчики', hintNative: 'Clue: pizza, fruit, pancakes' };
    }
  }

  if (q.includes('питомец') || (q.includes('вопрос') && q.includes('спросил'))) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Я бы спросил: как твои дела?', hintNative: 'You can say: I would ask: how are you doing?' };
      case 1:
        return { hintTarget: 'Как дела или что ты любишь?', hintNative: 'How are you or what do you like?' };
      case 2:
        return { hintTarget: 'Скажи: Я бы спросил...', hintNative: 'Say: I would ask...' };
      default:
        return { hintTarget: 'Подсказка: спроси о настроении или любимой еде', hintNative: 'Clue: ask about mood or favorite food' };
    }
  }

  if (q.includes('палочка') || q.includes('желани')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Я бы загадал полететь в космос.', hintNative: 'You can say: I would wish to fly into space.' };
      case 1:
        return { hintTarget: 'Полететь в космос или уметь летать?', hintNative: 'Fly into space or be able to fly?' };
      case 2:
        return { hintTarget: 'Скажи: Моё желание...', hintNative: 'Say: My wish is...' };
      default:
        return { hintTarget: 'Подсказка: путешествие, космос, радость', hintNative: 'Clue: travel, space, joy' };
    }
  }

  if (q.includes('путешествие') || q.includes('необычном')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Я хочу отправиться на воздушном шаре.', hintNative: 'You can say: I want to travel in a hot air balloon.' };
      case 1:
        return { hintTarget: 'На воздушном шаре или на ракете?', hintNative: 'In a balloon or on a rocket?' };
      case 2:
        return { hintTarget: 'Скажи: Я хочу отправиться на...', hintNative: 'Say: I want to travel on...' };
      default:
        return { hintTarget: 'Подсказка: воздушный шар, ракета, поезд', hintNative: 'Clue: balloon, rocket, train' };
    }
  }

  if (q.includes('куда')) {
    switch (lvl) {
      case 0:
        return { hintTarget: 'Можно сказать: Я хочу отправиться в горы.', hintNative: 'You can say: I want to go to the mountains.' };
      case 1:
        return { hintTarget: 'В горы или на море?', hintNative: 'To the mountains or to the sea?' };
      case 2:
        return { hintTarget: 'Скажи: Я хочу поехать в...', hintNative: 'Say: I want to go to...' };
      default:
        return { hintTarget: 'Подсказка: горы, море, далёкие острова', hintNative: 'Clue: mountains, sea, distant islands' };
    }
  }

  // 3. Animal specific properties
  const isGiraffe = obj === 'giraffe' || q.includes('жираф');
  const isBear = obj === 'polar_bear' || q.includes('медвед');
  const isParrot = obj === 'parrot' || q.includes('попуга');
  const isLion = obj === 'lion' || q.includes('лев');
  const isPenguin = obj === 'penguin' || q.includes('пингвин');
  const isZebra = obj === 'zebra' || q.includes('зебр');

  if (q.includes('какой') || q.includes('какая') || q.includes('какое') || q.includes('какие')) {
    if (isGiraffe) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Жираф высокий.', hintNative: 'You can say: The giraffe is tall.' };
        case 1: return { hintTarget: 'Высокий или низкий?', hintNative: 'Tall or short?' };
        case 2: return { hintTarget: 'Скажи: Жираф высокий.', hintNative: 'Say: The giraffe is tall.' };
        default: return { hintTarget: 'Подсказка: высокий', hintNative: 'Clue: tall' };
      }
    }
    if (isBear) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Белый медведь большой.', hintNative: 'You can say: The polar bear is big.' };
        case 1: return { hintTarget: 'Большой или маленький?', hintNative: 'Big or small?' };
        case 2: return { hintTarget: 'Скажи: Белый медведь большой.', hintNative: 'Say: The polar bear is big.' };
        default: return { hintTarget: 'Подсказка: большой', hintNative: 'Clue: big' };
      }
    }
    if (isParrot) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Попугай красивый.', hintNative: 'You can say: The parrot is beautiful.' };
        case 1: return { hintTarget: 'Красивый или серый?', hintNative: 'Beautiful or gray?' };
        case 2: return { hintTarget: 'Скажи: Попугай красивый.', hintNative: 'Say: The parrot is beautiful.' };
        default: return { hintTarget: 'Подсказка: красивый', hintNative: 'Clue: beautiful' };
      }
    }
    if (isLion) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Лев сильный.', hintNative: 'You can say: The lion is strong.' };
        case 1: return { hintTarget: 'Сильный или слабый?', hintNative: 'Strong or weak?' };
        case 2: return { hintTarget: 'Скажи: Лев сильный.', hintNative: 'Say: The lion is strong.' };
        default: return { hintTarget: 'Подсказка: сильный', hintNative: 'Clue: strong' };
      }
    }
    if (isPenguin) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Пингвин черно-белый.', hintNative: 'You can say: The penguin is black and white.' };
        case 1: return { hintTarget: 'Черно-белый или зеленый?', hintNative: 'Black and white or green?' };
        case 2: return { hintTarget: 'Скажи: Пингвин черно-белый.', hintNative: 'Say: The penguin is black and white.' };
        default: return { hintTarget: 'Подсказка: черно-белый', hintNative: 'Clue: black and white' };
      }
    }
    if (isZebra) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Зебра полосатая.', hintNative: 'You can say: The zebra is striped.' };
        case 1: return { hintTarget: 'Полосатая или пятнистая?', hintNative: 'Striped or spotted?' };
        case 2: return { hintTarget: 'Скажи: Зебра полосатая.', hintNative: 'Say: The zebra is striped.' };
        default: return { hintTarget: 'Подсказка: полосатая', hintNative: 'Clue: striped' };
      }
    }
  }

  if (q.includes('что делает') || q.includes('что он делает') || q.includes('что она делает')) {
    if (isGiraffe) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Жираф стоит.', hintNative: 'You can say: The giraffe is standing.' };
        case 1: return { hintTarget: 'Стоит или бежит?', hintNative: 'Standing or running?' };
        case 2: return { hintTarget: 'Скажи: Жираф стоит.', hintNative: 'Say: The giraffe is standing.' };
        default: return { hintTarget: 'Подсказка: стоит', hintNative: 'Clue: standing' };
      }
    }
    if (isBear) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Медведь гуляет.', hintNative: 'You can say: The bear is walking.' };
        case 1: return { hintTarget: 'Гуляет или спит?', hintNative: 'Walking or sleeping?' };
        case 2: return { hintTarget: 'Скажи: Медведь гуляет.', hintNative: 'Say: The bear is walking.' };
        default: return { hintTarget: 'Подсказка: гуляет', hintNative: 'Clue: walking' };
      }
    }
    if (isParrot) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Попугай летает.', hintNative: 'You can say: The parrot is flying.' };
        case 1: return { hintTarget: 'Летает или спит?', hintNative: 'Flying or sleeping?' };
        case 2: return { hintTarget: 'Скажи: Попугай летает.', hintNative: 'Say: The parrot is flying.' };
        default: return { hintTarget: 'Подсказка: летает', hintNative: 'Clue: flying' };
      }
    }
  }

  if (q.includes('где он') || q.includes('где живёт') || q.includes('где она')) {
    if (isGiraffe) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Жираф живёт в Африке.', hintNative: 'You can say: The giraffe lives in Africa.' };
        case 1: return { hintTarget: 'В Африке или на севере?', hintNative: 'In Africa or in the north?' };
        case 2: return { hintTarget: 'Скажи: Жираф живёт в Африке.', hintNative: 'Say: The giraffe lives in Africa.' };
        default: return { hintTarget: 'Подсказка: Африка', hintNative: 'Clue: Africa' };
      }
    }
    if (isBear) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Белый медведь живёт на севере.', hintNative: 'You can say: The polar bear lives in the north.' };
        case 1: return { hintTarget: 'На севере или в Африке?', hintNative: 'In the north or in Africa?' };
        case 2: return { hintTarget: 'Скажи: Медведь живёт на севере.', hintNative: 'Say: The bear lives in the north.' };
        default: return { hintTarget: 'Подсказка: север', hintNative: 'Clue: north' };
      }
    }
    if (isParrot) {
      switch (lvl) {
        case 0: return { hintTarget: 'Можно сказать: Попугай живёт в тёплом лесу.', hintNative: 'You can say: The parrot lives in a warm forest.' };
        case 1: return { hintTarget: 'В тёплом лесу или на снегу?', hintNative: 'In a warm forest or in the snow?' };
        case 2: return { hintTarget: 'Скажи: Попугай живёт в тёплом лесу.', hintNative: 'Say: The parrot lives in a warm forest.' };
        default: return { hintTarget: 'Подсказка: тёплый лес', hintNative: 'Clue: warm forest' };
      }
    }
  }

  if (q.includes('кто это') || q.includes('кто я')) {
    const animalName = isGiraffe ? 'жираф' : isBear ? 'белый медведь' : isParrot ? 'попугай' : isLion ? 'лев' : isPenguin ? 'пингвин' : isZebra ? 'зебра' : (obj || 'животное');
    const altAnimal = isGiraffe ? 'зебра' : isBear ? 'пингвин' : isParrot ? 'птица' : 'другое животное';
    switch (lvl) {
      case 0: return { hintTarget: `Можно сказать: Это ${animalName}.`, hintNative: `You can say: This is a ${animalName}.` };
      case 1: return { hintTarget: `${animalName} или ${altAnimal}?`, hintNative: `${animalName} or ${altAnimal}?` };
      case 2: return { hintTarget: 'Скажи: Это...', hintNative: 'Say: This is...' };
      default: return { hintTarget: `Подсказка: ${animalName}`, hintNative: `Clue: ${animalName}` };
    }
  }

  // 4. Initial card selector prompt before a card is chosen
  if (q.includes('картинка') && (q.includes('нравится') || q.includes('выбери'))) {
    switch (lvl) {
      case 0: return { hintTarget: 'Можно сказать: Мне нравится эта картинка.', hintNative: 'You can say: I like this picture.' };
      case 1: return { hintTarget: 'Картинка А или картинка Б?', hintNative: 'Picture A or picture B?' };
      case 2: return { hintTarget: 'Скажи: Мне нравится...', hintNative: 'Say: I like...' };
      default: return { hintTarget: 'Подсказка: выбери любую картинку, которая тебе интересна', hintNative: 'Clue: choose the picture that interests you' };
    }
  }

  // 5. Suitcase
  if (slide?.interactive_task === 'suitcase' || q.includes('чемодан')) {
    switch (lvl) {
      case 0: return { hintTarget: 'Можно сказать: Я положу куртку в чемодан.', hintNative: 'You can say: I will pack the jacket into the suitcase.' };
      case 1: return { hintTarget: 'Куртку или бинокль?', hintNative: 'The jacket or the binoculars?' };
      case 2: return { hintTarget: 'Скажи: В чемодан я положу...', hintNative: 'Say: Into the suitcase I will pack...' };
      default: return { hintTarget: 'Подсказка: выбери нужную вещь для путешествия', hintNative: 'Clue: choose an item needed for the trip' };
    }
  }

  // 6. Gift
  if (slide?.interaction_kind === 'gift_selector' || q.includes('подарок') || q.includes('мила')) {
    const giftLabel = obj.includes('book') || obj.includes('книг') ? 'книгу' :
                      obj.includes('flower') || obj.includes('букет') || obj.includes('цвет') ? 'букет' :
                      obj.includes('backpack') || obj.includes('рюкзак') ? 'рюкзак' :
                      obj.includes('teddy') || obj.includes('мишк') ? 'мишку' : '';
    if (giftLabel) {
      switch (lvl) {
        case 0: return { hintTarget: `Можно сказать: Мила привезла мне ${giftLabel}.`, hintNative: `You can say: Mila brought me a ${obj}.` };
        case 1: return { hintTarget: `Мила привезла ${giftLabel}?`, hintNative: `Did Mila bring a ${obj}?` };
        case 2: return { hintTarget: `Скажи: Мила привезла мне ${giftLabel}.`, hintNative: `Say: Mila brought me a ${obj}.` };
        default: return { hintTarget: `Подсказка: ${giftLabel}`, hintNative: `Clue: ${giftLabel}` };
      }
    }
    switch (lvl) {
      case 0: return { hintTarget: 'Можно сказать: Мила привезла мне подарок.', hintNative: 'You can say: Mila brought me a gift.' };
      case 1: return { hintTarget: 'Мишку или книгу?', hintNative: 'A teddy bear or a book?' };
      case 2: return { hintTarget: 'Скажи: Мила привезла мне...', hintNative: 'Say: Mila brought me...' };
      default: return { hintTarget: 'Подсказка: выбери подарок сверху и назови его', hintNative: 'Clue: choose a gift above and name it' };
    }
  }

  // 7. General ladder fallback
  const ladder = adaptiveQuestionForLevel(obj, lvl, slide);
  const baseModel = ladder.expectedAnswerModel || cleanChildFacingText(slide?.simplified_text) || 'Давай попробуем вместе!';
  switch (lvl) {
    case 0:
      return { hintTarget: `Можно сказать: ${baseModel}`, hintNative: `You can say: ${ladder.questionNative || baseModel}` };
    case 1:
      return { hintTarget: `${baseModel} или другой вариант?`, hintNative: `${ladder.questionNative || baseModel} or another option?` };
    case 2:
      return { hintTarget: `Скажи: ${baseModel}`, hintNative: `Say: ${ladder.questionNative || baseModel}` };
    default:
      return { hintTarget: `Подсказка: ${baseModel}`, hintNative: `Clue: ${ladder.questionNative || baseModel}` };
  }
}

export function buildAdaptiveHint(
  currentQuestion: string,
  currentObject: string,
  currentLevel: number,
  slide: any,
  targetLang = 'ru',
  nativeLang = 'ru'
): string {
  return buildAdaptiveHintPair(currentQuestion, currentObject, currentLevel, slide, targetLang, nativeLang).hintTarget;
}

/** Never let a translated target duplicate masquerade as support language. */
export function distinctSupportSpeech(target:string,support:string,targetLanguage:string,supportLanguage:string):string{
  if(normalizeRuntimeLanguage(targetLanguage)===normalizeRuntimeLanguage(supportLanguage))return '';
  const clean=(value:string)=>cleanChildFacingText(value).replace(/\s+/g,' ').trim();
  const targetText=clean(target);const supportText=clean(support);
  if(!supportText||supportText.localeCompare(targetText,undefined,{sensitivity:'base'})===0)return '';
  return supportText;
}

export function normalizeRuntimeLanguage(value:unknown,fallback='ru'):string{
  const code=String(value||'').trim().toLowerCase();
  return /^[a-z]{2,3}(?:-[a-z]{2,4})?$/.test(code)?code:String(fallback||'ru').toLowerCase();
}

/** The server snapshot wins for an active session; profile data is only the pre-session fallback. */
export function resolveRuntimeLanguagePair(profile:any,session?:any):RuntimeLanguagePair{
  const server=session?.language_pair||session?.languagePair||{};
  return {
    // The server snapshot is authoritative, so the same runtime supports any
    // configured target/support pair without a language-specific code path.
    targetLanguage:studiedLanguageForMobile(server.target_language??server.targetLanguage??profile?.learningLanguage??profile?.target_language),
    explanationLanguage:normalizeRuntimeLanguage(server.explanation_language??server.native_language??server.explanationLanguage??server.nativeLanguage??profile?.nativeLanguage??profile?.native_language,'ru'),
  };
}

/** Authored lesson text has a declared source language; it is never inferred from the child's target. */
export function authoredTextLanguage(lesson:any,slide?:any):string{
  return normalizeRuntimeLanguage(slide?.content_source_language||lesson?.content_source_language||lesson?.target_language,'ru');
}

export function localizedItemLabel(item:any,language:string,fallback=''):string{
  const code=normalizeRuntimeLanguage(language);
  const direct=item?.[`label_${code}`]??item?.[`answer_value_${code}`];
  if(direct)return String(direct);
  if(code==='ru')return String(item?.label_ru||item?.answer_value_ru||item?.label||fallback||item?.id||'');
  if(code==='en')return String(item?.label_en||item?.answer_value_en||item?.label||fallback||item?.id||'');
  return String(item?.label||fallback||item?.label_en||item?.label_ru||item?.id||'');
}

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
  return ['required_voice','optional_voice'].includes(String(slide?.answer_mode||''))||['voice_answer','required_movie_phrase','repeat','repeat_phrase','speak','dialogue','open_dialogue','roleplay','retell','continue_story','read_aloud','echo_reading','shared_reading','read_roles'].includes(String(slide?.type||''))||slide?.type==='card_selector'||slide?.type==='animal_compare'||slide?.type==='mood_choice';
}

export type ConversationTaskPolicy={mode:'single_answer'|'multi_turn';enabled:boolean;maxTurns:number;completionCondition:string};

/** Normalize the authored dialogue contract without inferring it from a turn number. */
export function conversationTaskPolicy(slide:any):ConversationTaskPolicy{
  const rawMode=String(slide?.conversation_mode||slide?.conversationMode||'').trim().toLowerCase();
  const explicitSingle=['single','single_answer','one_shot','none','off'].includes(rawMode);
  const legacyEnabled=slide?.allow_ai_followup===true||String(slide?.follow_up_policy||'').toLowerCase()==='optional';
  const isVoice=requiresVoice(slide);
  const enabled=slide?.suppress_ai_followup!==true&&!explicitSingle&&(['dialogue','conversation','multi_turn','roleplay'].includes(rawMode)||legacyEnabled||isVoice);
  const legacyFollowups=Math.max(0,Number(slide?.max_ai_followups||0)||0);
  const configured=slide?.max_turns??slide?.maxTurns;
  const parsed=configured===undefined||configured===null?(legacyFollowups?legacyFollowups+1:(enabled?5:1)):Number(configured);
  const maxTurns=enabled?Math.max(2,Math.min(8,Number.isFinite(parsed)?Math.trunc(parsed):5)):1;
  return {
    mode:enabled?'multi_turn':'single_answer',
    enabled,
    maxTurns,
    completionCondition:String(slide?.completion_condition||slide?.completionCondition||(enabled?'max_turns_or_natural_close':'after_first_accepted_answer')),
  };
}

/** The server turn being answered; generation of a follow-up already advanced it. */
export function conversationRecordingTurn(slide:any,currentTurn:number,stage:RuntimeStage,hasRecordedAnswer=false):number{
  const policy=conversationTaskPolicy(slide);
  // COMPLETE means that the authored requirement is satisfied; it does not
  // terminate an explicitly configured conversation. If the child presses
  // Answer again, preserve the current turn/history instead of silently
  // restarting an unrelated turn 0 dialogue.
  if(!policy.enabled)return 0;
  let turn=Math.max(0,Math.trunc(Number(currentTurn)||0));
  if(hasRecordedAnswer&&stage==='COMPLETE'){
    turn=Math.max(1,turn+1);
  }
  return Math.min(turn,policy.maxTurns-1);
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
  visual_metadata?:{label?:string;color_hint?:string;fact_hint?:string;image_key?:string};
};

export function buildVoiceRuntimeContext(slide:any,items:VoiceRuntimeItem[],selectedIds:string[],removedIds:string[],targetLanguage:string,interfaceLanguage:string):VoiceRuntimeContext{
  const visible=new Set(items.map(item=>String(item.id)));
  const selected=Array.from(new Set(selectedIds.map(String))).filter(id=>visible.has(id));
  const selectedSet=new Set(selected);
  // Extract visual_metadata from slide for factual AI checking (e.g. lion colour, animal species)
  let visual_metadata:VoiceRuntimeContext['visual_metadata'];
  const vm=slide?.visual_metadata;
  if(vm&&typeof vm==='object'){
    visual_metadata={
      ...(vm.label!=null?{label:String(vm.label)}:{}),
      ...(vm.color_hint!=null?{color_hint:String(vm.color_hint)}:{}),
      ...(vm.fact_hint!=null?{fact_hint:String(vm.fact_hint)}:{}),
      ...(vm.image_key!=null?{image_key:String(vm.image_key)}:{}),
    };
  } else {
    // Derive basic label from slide image_key or image_label as fallback
    const imgKey=String(slide?.image_key||slide?.image||slide?.photo||'');
    const imgLabel=String(slide?.image_label||slide?.label_en||slide?.label||'');
    if(imgKey||imgLabel) visual_metadata={...(imgKey?{image_key:imgKey}:{}),...(imgLabel?{label:imgLabel}:{})};
  }
  return {
    task_type:String(slide?.interactive_task||slide?.interaction_kind||slide?.type||'voice'),
    selection_policy:String(slide?.selection_policy||(slide?.interactive_task==='suitcase'?'child_choice':'authored_choice')),
    target_language:String(targetLanguage||''),
    interface_language:String(interfaceLanguage||''),
    visible_items:[...visible],
    selected_items:selected,
    removed_items:Array.from(new Set(removedIds.map(String))).filter(id=>visible.has(id)&&!selectedSet.has(id)),
    ...(visual_metadata?{visual_metadata}:{}),
  };
}

export const KNOWN_RUSSIAN_ACCUSATIVES:Record<string,string>={
  'мишка':'мишку',
  'куртка':'куртку',
  'бутылка воды':'бутылку воды',
  'бутылка':'бутылку',
  'камера':'камеру',
  'книга':'книгу',
  'рыба':'рыбу',
  'бинокль':'бинокль',
  'компас':'компас',
  'фотоаппарат':'фотоаппарат',
  'телескоп':'телескоп',
  'блокнот':'блокнот',
  'солнцезащитные очки':'солнцезащитные очки',
  'очки':'очки',
  'машина':'машину',
  'шляпа':'шляпу',
  'шапка':'шапку',
  'кепка':'кепку',
  'вода':'воду',
  'кошка':'кошку',
  'собака':'собаку',
  'черепаха':'черепаху',
  'птица':'птицу',
  'медведь':'медведя',
  'кот':'кота',
  'слон':'слона',
  'жираф':'жирафа',
  'пингвин':'пингвина',
  'зебра':'зебру',
  'попугай':'попугая',
  'лев':'льва',
};

export const KNOWN_ITEM_ACCUSATIVE_BY_ID:Record<string,string>={
  'teddy':'мишку',
  'jacket':'куртку',
  'water':'бутылку воды',
  'compass':'компас',
  'camera':'фотоаппарат',
  'telescope':'телескоп',
  'fish':'рыбу',
  'notebook':'блокнот',
  'binoculars':'бинокль',
  'sunglasses':'солнцезащитные очки',
  'book':'книгу',
};

export function russianAccusative(value:string,animate=false):string{
  const text=String(value||'').trim();
  if(!text)return '';
  const lower=text.toLowerCase();
  const directKnown=KNOWN_RUSSIAN_ACCUSATIVES[lower];
  if(directKnown){
    const firstChar=text.charAt(0);
    return firstChar&&firstChar===firstChar.toUpperCase()?directKnown.charAt(0).toUpperCase()+directKnown.slice(1):directKnown;
  }
  const words=text.split(/\s+/).filter(Boolean);
  if(!words.length)return text;
  if(words.length>1){
    const firstWord=words[0]||'';
    const firstLower=firstWord.toLowerCase();
    const firstKnown=KNOWN_RUSSIAN_ACCUSATIVES[firstLower];
    if(firstKnown){
      let firstAcc=firstKnown;
      const firstChar=firstWord.charAt(0);
      if(firstChar&&firstChar===firstChar.toUpperCase())firstAcc=firstAcc.charAt(0).toUpperCase()+firstAcc.slice(1);
      return [firstAcc,...words.slice(1)].join(' ');
    }
    const lastChar=firstWord.slice(-1);
    if(firstLower.endsWith('а')){
      const firstChanged=firstWord.slice(0,-1)+(lastChar==='А'?'У':'у');
      return [firstChanged,...words.slice(1)].join(' ');
    }
    if(firstLower.endsWith('я')){
      const firstChanged=firstWord.slice(0,-1)+(lastChar==='Я'?'Ю':'ю');
      return [firstChanged,...words.slice(1)].join(' ');
    }
  }
  const word=words[words.length-1]||'';
  const wordLower=word.toLowerCase();
  if(/(ки|ги|хи|ы|и|очки)$/i.test(wordLower))return text;
  let changed=word;
  const lastChar=word.slice(-1);
  if(wordLower.endsWith('а')){
    changed=word.slice(0,-1)+(lastChar==='А'?'У':'у');
  }else if(wordLower.endsWith('я')){
    changed=word.slice(0,-1)+(lastChar==='Я'?'Ю':'ю');
  }else if(animate&&wordLower.endsWith('ь')){
    changed=word.slice(0,-1)+(lastChar==='Ь'?'Я':'я');
  }else if(animate&&/[бвгджзклмнпрстфхцчшщ]$/i.test(wordLower)){
    changed=word+(lastChar===lastChar.toUpperCase()?'А':'а');
  }
  return [...words.slice(0,-1),changed].join(' ');
}

export function buildSelectedItemPhrase(item:any, childGender='boy'):string{
  const isGirl=String(childGender||'').trim().toLowerCase()==='girl';
  const verb=isGirl?'выбрала':'выбрал';
  let accusative='';
  if(item&&typeof item==='object'){
    accusative=String(
      item.labelAccusative||item.label_accusative||item.label_ru_accusative||''
    ).trim();
    if(!accusative){
      const id=String(item.id||'').toLowerCase();
      if(KNOWN_ITEM_ACCUSATIVE_BY_ID[id]){
        accusative=KNOWN_ITEM_ACCUSATIVE_BY_ID[id];
      }else{
        const nom=String(item.labelNominative||item.label_nominative||item.label_ru||item.label||'').trim();
        accusative=nom?russianAccusative(nom,true):'';
      }
    }
  }else{
    const raw=String(item||'').trim();
    const rawId=raw.toLowerCase();
    if(KNOWN_ITEM_ACCUSATIVE_BY_ID[rawId]){
      accusative=KNOWN_ITEM_ACCUSATIVE_BY_ID[rawId];
    }else{
      accusative=russianAccusative(raw,true);
    }
  }
  return `Ты ${verb} ${accusative}.`;
}

export function formatChoiceReplica(
  item:any,
  gender='boy',
  targetLanguage='ru',
  action:'chose'|'why_chose'='chose',
  punctuation='.'
):string{
  const isGirl=String(gender||'').trim().toLowerCase()==='girl';
  const lang=String(targetLanguage||'ru').trim().toLowerCase().slice(0,2);
  if(lang==='ru'){
    if(action==='why_chose'){
      const verb=isGirl?'выбрала':'выбрал';
      let acc='';
      if(item&&typeof item==='object'){
        acc=String(item.labelAccusative||item.label_accusative||item.label_ru_accusative||'').trim();
        if(!acc){
          const id=String(item.id||'').toLowerCase();
          acc=KNOWN_ITEM_ACCUSATIVE_BY_ID[id]||russianAccusative(String(item.labelNominative||item.label_nominative||item.label_ru||item.label||''),true);
        }
      }else{
        const raw=String(item||'').trim();
        acc=KNOWN_ITEM_ACCUSATIVE_BY_ID[raw.toLowerCase()]||russianAccusative(raw,true);
      }
      return `Почему ты ${verb} ${acc}?`;
    }
    const phrase=buildSelectedItemPhrase(item,gender);
    if(punctuation&&punctuation!=='.'){
      return phrase.slice(0,-1)+punctuation;
    }
    return phrase;
  }else{
    let labelEn='';
    if(item&&typeof item==='object'){
      labelEn=String(item.label_en||item.label||item.id||'').trim();
    }else{
      labelEn=String(item||'').trim();
    }
    if(action==='why_chose')return `Why did you choose ${labelEn}?`;
    const punc=['.','!','?'].includes(punctuation)?punctuation:'.';
    return `You chose ${labelEn}${punc}`;
  }
}

export function childIdeaPrompt(itemLabel:string,taskType:string,gender='boy'):string{
  const label=String(itemLabel||'').trim();
  if(String(taskType)==='animal_compare')return label?`Что ты хочешь сказать про ${label}?`:'Что ты хочешь сказать про это животное?';
  if(String(taskType)==='suitcase')return label?formatChoiceReplica(label,gender,'ru','chose','.'):'Что ты хочешь сказать про свой выбор?';
  return label?`Что ты хочешь сказать про ${label}?`:'Что ты хочешь сказать?';
}

export function stageAfterTutorSpeech(slide:any,hasSelection=false):RuntimeStage{
  if(requiresSelection(slide)&&!hasSelection)return 'WAITING_ACTION';
  if(requiresVoice(slide))return 'WAITING_VOICE';
  return 'COMPLETE';
}

export function recordEnabled(stage:RuntimeStage,slide:any,hasSelection=false):boolean{
  const isRiddle=Array.isArray(slide?.riddle_options);
  return stage==='WAITING_VOICE'&&requiresVoice(slide)&&(!requiresSelection(slide)||hasSelection||isRiddle);
}

export function answerEnabled(stage:RuntimeStage,slide:any,hasSelection=false,busy=false,recording=false):boolean{
  if(busy||recording||!requiresVoice(slide))return false;
  const isRiddle=Array.isArray(slide?.riddle_options);
  if(requiresSelection(slide)&&!hasSelection&&!isRiddle)return false;
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

export function tutorAudioFailureStage(after:RuntimeStage):RuntimeStage{
  return after==='AI_SPEAKING'?'WAITING_VOICE':after;
}

export function tutorAudioErrorCode(error:unknown):string{
  const message=String((error as any)?.message||error||'').toUpperCase();
  if(message.includes('TTS_CACHE_TIMEOUT'))return 'TTS_CACHE_TIMEOUT';
  if(message.includes('TTS_DOWNLOAD_HTTP_503'))return 'TTS_SERVICE_UNAVAILABLE';
  if(message.includes('TTS_DOWNLOAD_HTTP_'))return 'TTS_DOWNLOAD_FAILED';
  if(message.includes('TIMEOUT'))return 'TTS_PLAYBACK_TIMEOUT';
  return 'TTS_PLAYBACK_FAILED';
}

export type MicrophonePermissionDecision='record'|'request'|'settings';
export function microphonePermissionDecision(granted:boolean,canAskAgain:boolean):MicrophonePermissionDecision{
  if(granted)return 'record';
  return canAskAgain?'request':'settings';
}

export type RecordingGateState={speechStarted:boolean;silenceStartedAt:number|null;stopReason:'SPEECH_COMPLETE'|'SAFETY_LIMIT'|null};
export type VoiceUploadState='IDLE'|'RECORDING'|'FINALIZING'|'LOCAL_READY'|'UPLOADING'|'UPLOAD_FAILED'|'ACKNOWLEDGED';
export type VoiceUploadEvent='START'|'STOP'|'LOCAL_FINALIZED'|'UPLOAD'|'FAIL'|'ACK'|'RESET';

export function voiceUploadTransition(state:VoiceUploadState,event:VoiceUploadEvent):VoiceUploadState{
  if(event==='RESET')return 'IDLE';
  if(event==='START'&&['IDLE','ACKNOWLEDGED'].includes(state))return 'RECORDING';
  if(event==='STOP'&&state==='RECORDING')return 'FINALIZING';
  if(event==='LOCAL_FINALIZED'&&state==='FINALIZING')return 'LOCAL_READY';
  if(event==='UPLOAD'&&['LOCAL_READY','UPLOAD_FAILED'].includes(state))return 'UPLOADING';
  if(event==='FAIL'&&state==='UPLOADING')return 'UPLOAD_FAILED';
  if(event==='ACK'&&state==='UPLOADING')return 'ACKNOWLEDGED';
  return state;
}

export function recorderDurationForGate(recordingStartedAt:number,nowMillis:number,reportedDurationMillis:number):number{
  const elapsed=Math.max(0,nowMillis-recordingStartedAt);return Math.max(0,Math.min(Number(reportedDurationMillis||0),elapsed+250));
}

export function voiceUploadFailureStage(requiredForMovie:boolean):RuntimeStage{return requiredForMovie?'WAITING_VOICE':'COMPLETE'}

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
  if(slide?.interactive_task==='suitcase')return true;
  if(slide?.voice_after_action_optional===true)return false;
  return slide?.requiredForMovie===true||slide?.required_for_movie===true||Boolean(slide?.required_phrase_id&&slide?.allow_skip===false);
}

export function nextEnabled(stage:RuntimeStage,visualReady=true,policy:NextPolicy={requiredForMovie:false}):boolean{
  if(!visualReady)return false;
  // A semantic/task COMPLETE state is not proof that an exact movie take was
  // persisted. Required movie steps remain blocked until that recording is
  // acknowledged by the backend, regardless of the visible runtime stage.
  if(policy.requiredForMovie===true&&policy.hasValidRecording!==true)return false;
  if(policy.mode==='after_action')return stage==='COMPLETE'||(policy.hasValidRecording===true&&stage!=='WAITING_ACTION');
  if(policy.mode==='after_answer')return stage==='COMPLETE'||policy.hasValidRecording===true;
  return true;
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

export type LessonBootstrapStage='LESSON_SCHEMA'|'SESSION_START'|'VERSION_CHECK';

export function lessonBootstrapErrorCode(error:any,stage:LessonBootstrapStage):string{
  const status=Number(error?.status||0);
  const backend=String(error?.code||'').replace(/[^A-Z0-9_]/gi,'_').toUpperCase().slice(0,60);
  if(status>0)return `${stage}_HTTP_${status}${backend?`_${backend}`:''}`;
  const message=String(error?.message||'');
  if(message==='LESSON_VERSION_MISMATCH')return 'VERSION_CHECK_LESSON_VERSION_MISMATCH';
  if(/timeout/i.test(message))return `${stage}_TIMEOUT`;
  if(/network|fetch/i.test(message))return `${stage}_NETWORK`;
  return `${stage}_RUNTIME`;
}

export function recoveryStageAfterFailure(slide:any,hasSelection=false):RuntimeStage{
  if(requiresSelection(slide)&&!hasSelection)return 'WAITING_ACTION';
  if(requiresVoice(slide))return 'WAITING_VOICE';
  return 'COMPLETE';
}

export type ProgressiveHint={step:'REPHRASE'|'CHOICES'|'MODEL'|'RECOVER';prompt:string};

export function progressiveHint(slide:any,attempt:number):ProgressiveHint{
  const examplesAllowed=slide?.examples_allowed!==false;
  const examples=examplesAllowed?(slide?.adaptive_models||slide?.target_language_options||slide?.model_examples||[]).map((item:any)=>String(item?.text||item||'').trim()).filter(Boolean).filter((text:string)=>!text.includes('?')).slice(0,5):[];
  const example=String(examples[0]||slide?.hint_example_target||slide?.simplified_text||slide?.model_answer_target||'').trim();
  const step=Math.max(1,Number(attempt)||1);
  // A hint is an answer model, never a paraphrased question. Repeated help
  // moves down the same model ladder rather than asking the child to think.
  if(examplesAllowed&&example)return {step:'MODEL',prompt:example};
  if(!examplesAllowed)return {step:'RECOVER',prompt:'Спасибо за попытку. Продолжим без готового примера.'};
  return {step:'RECOVER',prompt:''};
}

export function adaptiveModelPhrase(slide:any,languageLevel='PRE_A1',difficulty=.15):string{
  const simple=String(slide?.model_examples?.[0]||slide?.simplified_text||slide?.model_answer_target||slide?.task_goal||slide?.question||'').trim();
  const richer=String(slide?.richer_model_text||slide?.model_answer_richer||'').trim();
  return richer&&String(languageLevel).toUpperCase()!=='PRE_A1'&&difficulty>=.45?richer:simple;
}

export function manualHintSource(slide:any,languageLevel='PRE_A1',difficulty=.15,selectedItem?:string,sourceLanguage=authoredTextLanguage(undefined,slide)):SourcedRuntimeText{
  const language=normalizeRuntimeLanguage(sourceLanguage);
  const options=(slide?.target_language_options||slide?.model_examples||[]).map((item:any)=>String(item?.text||item||'').trim()).filter(Boolean);
  if(selectedItem){
    const cleanItem=String(selectedItem).toLowerCase().replace(/_/g,' ').trim();
    const matched=options.find((opt:string)=>opt.toLowerCase().includes(cleanItem));
    if(matched)return {text:matched,sourceLanguage:language};
    const dragItem=slide?.drag_items?.find?.((it:any)=>String(it.id).toLowerCase()===cleanItem||String(it.label_en||'').toLowerCase()===cleanItem||String(it.label_ru||'').toLowerCase()===cleanItem)||slide?.selection_options?.find?.((it:any)=>String(it.id).toLowerCase()===cleanItem||String(it.label_en||it.label||'').toLowerCase()===cleanItem||String(it.label_ru||it.answer_value_ru||'').toLowerCase()===cleanItem);
    if(dragItem){
      const label=localizedItemLabel(dragItem,language,cleanItem);
      if(language==='ru')return {text:String(slide?.interactive_task)==='suitcase'?`Можно сказать: Я положил(а) в чемодан ${label}.`:`Можно сказать: Я выбираю ${label}.`,sourceLanguage:language};
      if(language==='en')return {text:`You can say: I chose ${label}.`,sourceLanguage:language};
    }
    if((slide?.kind==='gift'||slide?.interactive_task==='gift'||slide?.slide_id==='slide_20')&&language==='ru'){
      return {text:`Можно сказать: Мила подарила мне ${cleanItem}.`,sourceLanguage:language};
    }
    if((slide?.kind==='gift'||slide?.interactive_task==='gift'||slide?.slide_id==='slide_20')&&language==='en'){
      return {text:`You can say: Mila gave me ${cleanItem}.`,sourceLanguage:language};
    }
  }
  if(String(languageLevel).toUpperCase()!=='PRE_A1'&&difficulty>=.45){
    return {text:String(slide?.richer_model_text||slide?.model_answer_richer||options[1]||options[0]||adaptiveModelPhrase(slide,languageLevel,difficulty)).trim(),sourceLanguage:language};
  }
  return {text:String(slide?.hint_example_target||slide?.hint_target||options[0]||adaptiveModelPhrase(slide,languageLevel,difficulty)).trim(),sourceLanguage:language};
}

/** Backward-compatible string accessor for callers that do not need source metadata. */
export function manualHintExample(slide:any,languageLevel='PRE_A1',difficulty=.15,selectedItem?:string):string{
  return manualHintSource(slide,languageLevel,difficulty,selectedItem).text;
}

export function advanceAfterAssessment(response:{accepted?:boolean;advance_allowed?:boolean;needs_retry?:boolean;follow_up_question?:string;tutor_turn?:{follow_up_target?:string}}):'FOLLOW_UP'|'COMPLETE'|'RETRY'{
  const followUp=String(response.tutor_turn?.follow_up_target||response.follow_up_question||'').trim();
  if(response.accepted&&followUp)return 'FOLLOW_UP';
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
  if(phase==='retry'&&simplified)return simplified;
  // Legacy callers still receive a safe first rung instead of an advanced
  // open prompt. New callers should use adaptivePromptPlan with the profile.
  if(phase==='initial'&&(String(_languageLevel).toUpperCase()==='PRE_A1'||_difficulty<.25)){
    const models=(slide?.adaptive_models||slide?.model_examples||[]).map(adaptiveText).filter(Boolean);
    return models[0]||simplified||authored;
  }
  return authored;
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

export type ContainedMediaFrame={left:number;top:number;width:number;height:number;aspectRatio:number};

/** Fit a source into its available canvas without crop, stretch, or scroll. */
export function containedMediaFrame(containerWidth:number,containerHeight:number,sourceAspect=16/9):ContainedMediaFrame{
  const width=Math.max(0,Number(containerWidth)||0);const height=Math.max(0,Number(containerHeight)||0);const aspect=Math.max(.2,Math.min(5,Number(sourceAspect)||16/9));
  if(width<=0||height<=0)return {left:0,top:0,width:0,height:0,aspectRatio:aspect};
  const fittedWidth=Math.min(width,height*aspect);const fittedHeight=fittedWidth/aspect;
  return {left:(width-fittedWidth)/2,top:(height-fittedHeight)/2,width:fittedWidth,height:fittedHeight,aspectRatio:aspect};
}

export type SuitcaseFitLayout={columns:number;rows:number;itemSize:number;packedItemSize:number;targetHeight:number;itemsHeight:number;totalHeight:number};
export function suitcaseFitLayout(width:number,height:number,itemCount:number):SuitcaseFitLayout{
  const safeWidth=Math.max(180,Number(width)||180);const safeHeight=Math.max(150,Number(height)||150);const count=Math.max(1,Math.floor(itemCount||1));
  const columns=Math.min(count,count>=8?5:count>=5?4:count);const rows=Math.max(1,Math.ceil(count/columns));const labelHeight=22;const gap=6;
  const targetHeight=Math.min(98,Math.max(62,Math.floor(safeHeight*.4)));const cellWidth=Math.floor(safeWidth/columns);
  const availableItemsHeight=Math.max(rows*24,safeHeight-targetHeight-labelHeight-gap);const itemSize=Math.max(24,Math.min(50,cellWidth-4,Math.floor(availableItemsHeight/rows)-2));
  const itemsHeight=rows*(itemSize+2);const packedRows=Math.max(1,Math.ceil(count/columns));const packedItemSize=Math.max(22,Math.min(itemSize-4,cellWidth-8,Math.floor((targetHeight-8)/packedRows)));
  return {columns,rows,itemSize,packedItemSize,targetHeight,itemsHeight,totalHeight:targetHeight+labelHeight+gap+itemsHeight};
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
