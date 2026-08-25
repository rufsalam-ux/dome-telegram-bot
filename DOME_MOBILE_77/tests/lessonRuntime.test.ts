import assert from 'node:assert/strict';
import test from 'node:test';

import {
  adaptiveCardQuestionText,
  advanceAfterAssessment,
  cardQuestions,
  cardSelectionAllowed,
  cardVoiceKey,
  dropInsideTarget,
  heroBox,
  initialBilingualHint,
  lessonLayoutPolicy,
  movedPixelRect,
  nextCardQuestion,
  nextEnabled,
  recordEnabled,
  rectanglesOverlap,
  requiresSelection,
  requiresVoice,
  RUNTIME_STAGES,
  runtimePrompt,
  stageAfterTutorSpeech,
  suitcaseDropOutcome,
  suitcaseDropAccepted,
  suitcaseTapFallbackAvailable,
  tutorAudioTransition,
  tutorAudioWatchdogStage,
  updatePackedItems,
  visualRequiredForSlide,
} from '../src/engine/lessonRuntime.ts';
import bundledLesson from '../src/data/botLesson.json' with {type:'json'};
import {buildRuntimeOrder} from '../src/data/lessonInteractions.ts';
import {beginVisualAssetLoad,failVisualAsset,loadVisualAssetWithRetry,useLocalizedVisualAsset,visualAssetSourceForKey} from '../src/engine/visualAsset.ts';

const greeting={type:'guided_speaking',answer_mode:'required_voice',adaptive:true,bot_says_target:'Привет! Я рада тебя видеть. Как ты сегодня себя чувствуешь?',simplified_text:'Привет! У меня всё хорошо.'};
const cards={slide_id:'slide_09',type:'card_selector',answer_mode:'none',card_question_sets:{A:[{id:'A1',text:'Назови три прилагательных.',pre_a1_text:'Ты добрый или весёлый?'},{id:'A2',text:'Второй?'},{id:'A3',text:'Третий?'}]}};
const mila={slide_id:'slide_20',answer_mode:'required_voice',interaction_kind:'gift_selector',selection_options:[{id:'book'}],hero_placement:'left_of_mila',hero_box:[0.04,0.35,0.24,0.61]};

test('AI speech keeps recording disabled',()=>{
  assert.equal(recordEnabled('AI_SPEAKING',greeting,true),false);
});

test('recording becomes available only after tutor speech finishes',()=>{
  assert.equal(stageAfterTutorSpeech(greeting,true),'WAITING_VOICE');
  assert.equal(recordEnabled('WAITING_VOICE',greeting,true),true);
});

test('PRE_A1 opening retains the complete authored question',()=>{
  const prompt=runtimePrompt(greeting,'PRE_A1',0.12,'initial');
  assert.match(prompt,/Как ты сегодня себя чувствуешь/);
  assert.notEqual(prompt,'Привет! У меня всё хорошо.');
  assert.equal(runtimePrompt(greeting,'PRE_A1',0.12,'retry'),'Привет! У меня всё хорошо.');
});

test('PRE_A1 receives one short immediate home-language duplicate',()=>{
  assert.equal(initialBilingualHint('Как ты сегодня себя чувствуешь? Потом расскажи подробно.','PRE_A1',0.12),'Как ты сегодня себя чувствуешь?');
  assert.equal(initialBilingualHint('Как ты сегодня себя чувствуешь?','A1',0.45),'');
});

test('selected card has a deterministic three-question flow and stable voice keys',()=>{
  const questions=cardQuestions(cards,'A');
  assert.deepEqual(questions.map(item=>item.id),['A1','A2','A3']);
  assert.equal(cardVoiceKey('slide_09','A',questions[0]!), 'slide_09:A:A1');
  assert.equal(nextCardQuestion(cards,'A',1).question?.id,'A3');
  assert.equal(nextCardQuestion(cards,'A',2).done,true);
});

test('Mila cannot dead-end when cached Android audio finishes between status polls',()=>{
  let stage=stageAfterTutorSpeech(mila,false);assert.equal(stage,'WAITING_ACTION');
  stage='AI_SPEAKING';assert.equal(recordEnabled(stage,mila,true),false);
  const transition=tutorAudioTransition(stage,{playing:false,isBuffering:false,didJustFinish:true},false,'WAITING_VOICE');
  assert.equal(transition.finished,true);stage=transition.stage;
  assert.equal(stage,'WAITING_VOICE');assert.equal(recordEnabled(stage,mila,true),true);
  stage='PROCESSING';assert.equal(recordEnabled(stage,mila,true),false);
  stage=advanceAfterAssessment({accepted:true});assert.equal(stage,'COMPLETE');
  assert.equal(nextEnabled(stage),true);
});

test('audio watchdog never unlocks while speech is playing but prevents a missed-status dead end',()=>{
  assert.equal(tutorAudioWatchdogStage('AI_SPEAKING',{playing:true,isBuffering:false},'WAITING_VOICE'),'AI_SPEAKING');
  assert.equal(tutorAudioWatchdogStage('AI_SPEAKING',{playing:false,isBuffering:true},'WAITING_VOICE'),'AI_SPEAKING');
  assert.equal(tutorAudioWatchdogStage('AI_SPEAKING',{playing:false,isBuffering:false},'WAITING_VOICE'),'WAITING_VOICE');
});

test('third unsupported take advances without being accepted',()=>{
  assert.equal(advanceAfterAssessment({accepted:false,advance_allowed:true,needs_retry:false}),'COMPLETE');
  assert.equal(advanceAfterAssessment({accepted:false,advance_allowed:false,needs_retry:true}),'RETRY');
});

test('Mila hero placement is declarative and left of Mila',()=>{
  assert.deepEqual(heroBox(mila,{default_hero_placement:'hidden'}),[0.04,0.35,0.24,0.61]);
  assert.equal(heroBox({}, {default_hero_placement:'hidden'}),null);
  const authored=(bundledLesson.slides as any[]).find(slide=>slide.slide_id==='slide_20');
  const resolved=heroBox(authored,bundledLesson) as [number,number,number,number];
  assert.ok(resolved[0]+resolved[2]<Number(authored.character_box[0]));
  for(const item of authored.selection_options)assert.equal(rectanglesOverlap(resolved,item.rect),false);
});

test('portrait and landscape keep controls pinned and readable',()=>{
  const portrait=lessonLayoutPolicy(360,640,24);const landscape=lessonLayoutPolicy(800,360,10);
  assert.equal(portrait.landscape,false);assert.equal(landscape.landscape,true);
  assert.equal(portrait.controlsPinned,true);assert.equal(landscape.controlsPinned,true);
  assert.ok(portrait.bottomPadding>=24);assert.ok(landscape.controlFlex>0);assert.equal(portrait.visualFlex,0);
  assert.ok(portrait.visualMaxHeight<640);assert.ok(portrait.visualMinHeight>0);assert.ok(portrait.visualMinHeight<=portrait.visualMaxHeight);
});

test('PRE_A1 cards use one concrete choice and a selected card cannot be replaced mid-flow',()=>{
  const question=cardQuestions(cards,'A')[0];
  assert.equal(adaptiveCardQuestionText(question,'PRE_A1',0.15),'Ты добрый или весёлый?');
  assert.equal(adaptiveCardQuestionText(question,'A2',0.55),'Назови три прилагательных.');
  assert.equal(cardSelectionAllowed('WAITING_ACTION',''),true);
  assert.equal(cardSelectionAllowed('WAITING_VOICE','A'),false);
  assert.equal(cardSelectionAllowed('WAITING_ACTION','A'),false);
});

test('visual asset shows bundled original immediately and falls back after localized failure',()=>{
  const original={bundle:'slide-01.png'};const remote={uri:'https://example.test/slide-01.png'};
  const loading=beginVisualAssetLoad(original,true,'slide-01.png');
  assert.equal(loading.source,original);assert.equal(loading.status,'loading');assert.equal(loading.kind,'original');
  const localized=useLocalizedVisualAsset(loading,remote);
  assert.equal(localized.source,remote);assert.equal(localized.kind,'localized');
  const fallback=failVisualAsset(localized,'network error');
  assert.equal(fallback.source,original);assert.equal(fallback.status,'fallback');assert.equal(fallback.kind,'original');
  const nextOriginal={bundle:'slide-03.png'};
  assert.equal(visualAssetSourceForKey(fallback,'slide-03.png',nextOriginal),nextOriginal);
});

test('visual asset without bundled source exposes a non-empty placeholder state',()=>{
  const loading=beginVisualAssetLoad(undefined,true);assert.equal(loading.status,'loading');assert.equal(loading.source,undefined);
  const failed=failVisualAsset(loading,'not found');assert.equal(failed.status,'unavailable');assert.equal(failed.source,undefined);
});

test('visual preload retries once and critical visuals gate NEXT instead of showing blank content',async()=>{
  let calls=0;
  const loaded=await loadVisualAssetWithRetry(async()=>{calls+=1;if(calls===1)throw new Error('slow edge');return 'cached'},2,100,1);
  assert.equal(loaded,'cached');assert.equal(calls,2);
  assert.equal(visualRequiredForSlide(mila),true);
  assert.equal(nextEnabled('COMPLETE',false),false);assert.equal(nextEnabled('COMPLETE',true),true);
});

test('opening slide has a safe declarative hero placement and bundled source',()=>{
  const opening=(bundledLesson.slides as any[]).find(slide=>slide.slide_id==='slide_01');
  assert.equal(opening.image,'lesson-images/slide-01.png');
  const resolved=heroBox(opening,bundledLesson) as [number,number,number,number];assert.ok(resolved);
  for(const box of opening.content_boxes||[])assert.equal(rectanglesOverlap(resolved,box),false);
});

test('runtime has the authored conversation state machine and follow-up transition',()=>{
  assert.deepEqual(RUNTIME_STAGES,['ENTER','AI_SPEAKING','WAITING_ACTION','WAITING_VOICE','PROCESSING','FEEDBACK','FOLLOW_UP','RETRY','COMPLETE']);
  assert.equal(advanceAfterAssessment({accepted:true,tutor_turn:{follow_up_target:'Why?'}}),'FOLLOW_UP');
});

test('hero collision falls back and drag hit testing uses real target bounds',()=>{
  const slide={hero_anchor:'left',content_boxes:[[0,0.3,0.3,0.7]],hero_fallback_anchors:['right']};
  assert.deepEqual(heroBox(slide,{default_hero_placement:'hidden'}),[0.76,0.34,0.22,0.62]);
  assert.equal(rectanglesOverlap([0,0,0.2,0.2],[0.1,0.1,0.2,0.2]),true);
  assert.equal(dropInsideTarget(150,130,{x:100,y:100,width:100,height:60}),true);
  assert.equal(dropInsideTarget(40,40,{x:100,y:100,width:100,height:60}),false);
  assert.equal(suitcaseDropOutcome(false,true),'PACK');
  assert.equal(suitcaseDropOutcome(true,false),'UNPACK');
  assert.equal(suitcaseDropOutcome(false,false),'RETURN');
});

test('Android suitcase drag atomically packs, persists visually, unpacks, and offers a late tap fallback',()=>{
  const target={x:20,y:40,width:300,height:130};const origin={x:44,y:250,width:58,height:58};
  const moved=movedPixelRect(origin,80,-112)!;
  // The item overlaps the target even if Android reports a stale/missing
  // release point. This is the regression that previously returned it.
  assert.equal(suitcaseDropAccepted(undefined,moved,target),true);
  let packed:string[]=[];const packOutcome=suitcaseDropOutcome(false,true);
  packed=updatePackedItems(packed,'water',packOutcome);
  assert.deepEqual(packed,['water']);assert.equal(packed.includes('water'),true);
  assert.equal(updatePackedItems(packed,'water','RETURN'),packed);
  packed=updatePackedItems(packed,'water',suitcaseDropOutcome(true,false));assert.deepEqual(packed,[]);
  assert.equal(suitcaseTapFallbackAvailable(2),false);assert.equal(suitcaseTapFallbackAvailable(3),true);
});

test('programmatic demo flow has no early record, duplicate answer, or dead end',()=>{
  let stage=stageAfterTutorSpeech(cards,false);assert.equal(stage,'WAITING_ACTION');assert.equal(recordEnabled(stage,cards,false),false);
  stage=stageAfterTutorSpeech(cards,true);assert.equal(stage,'WAITING_VOICE');assert.equal(recordEnabled(stage,cards,true),true);
  stage='PROCESSING';assert.equal(recordEnabled(stage,cards,true),false);
  for(let question=0;question<3;question++){
    const outcome=advanceAfterAssessment({accepted:question!==1,advance_allowed:question===1});assert.equal(outcome,'COMPLETE');
    const next=nextCardQuestion(cards,'A',question);stage=next.done?'COMPLETE':'WAITING_VOICE';
  }
  assert.equal(stage,'COMPLETE');assert.equal(nextEnabled(stage),true);
});

test('scripted QA can traverse every active demo_001 runtime slide',()=>{
  const order=buildRuntimeOrder(bundledLesson.slides as any[]);
  assert.ok(order.length>20);
  for(const slide of order){
    let stage=stageAfterTutorSpeech(slide,false);
    if(requiresSelection(slide)){
      assert.equal(stage,'WAITING_ACTION',`${slide.slide_id} must wait for its real action`);
      assert.equal(recordEnabled(stage,slide,false),false);
      stage=stageAfterTutorSpeech(slide,true);
    }
    if(requiresVoice(slide)){
      assert.equal(stage,'WAITING_VOICE',`${slide.slide_id} must wait for a real voice take`);
      assert.equal(recordEnabled(stage,slide,true),true);
      stage=advanceAfterAssessment({accepted:true});
    }
    assert.equal(stage,'COMPLETE',`${slide.slide_id} has a reachable completion`);
    assert.equal(nextEnabled(stage),true);
  }
});
