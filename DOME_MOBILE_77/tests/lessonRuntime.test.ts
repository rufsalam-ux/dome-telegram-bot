import assert from 'node:assert/strict';
import test from 'node:test';

import {
  advanceAfterAssessment,
  cardQuestions,
  cardVoiceKey,
  dropInsideTarget,
  heroBox,
  lessonLayoutPolicy,
  nextCardQuestion,
  nextEnabled,
  recordEnabled,
  rectanglesOverlap,
  requiresSelection,
  requiresVoice,
  RUNTIME_STAGES,
  runtimePrompt,
  stageAfterTutorSpeech,
} from '../src/engine/lessonRuntime.ts';
import bundledLesson from '../src/data/botLesson.json' with {type:'json'};
import {buildRuntimeOrder} from '../src/data/lessonInteractions.ts';

const greeting={type:'guided_speaking',answer_mode:'required_voice',adaptive:true,bot_says_target:'Привет! Я рада тебя видеть. Как ты сегодня себя чувствуешь?',simplified_text:'Привет! У меня всё хорошо.'};
const cards={slide_id:'slide_09',type:'card_selector',answer_mode:'none',card_question_sets:{A:[{id:'A1',text:'Первый?'},{id:'A2',text:'Второй?'},{id:'A3',text:'Третий?'}]}};
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

test('selected card has a deterministic three-question flow and stable voice keys',()=>{
  const questions=cardQuestions(cards,'A');
  assert.deepEqual(questions.map(item=>item.id),['A1','A2','A3']);
  assert.equal(cardVoiceKey('slide_09','A',questions[0]!), 'slide_09:A:A1');
  assert.equal(nextCardQuestion(cards,'A',1).question?.id,'A3');
  assert.equal(nextCardQuestion(cards,'A',2).done,true);
});

test('Mila cannot dead-end: selection leads to voice, completed take leads to NEXT',()=>{
  assert.equal(stageAfterTutorSpeech(mila,false),'WAITING_ACTION');
  assert.equal(stageAfterTutorSpeech(mila,true),'WAITING_VOICE');
  assert.equal(recordEnabled('WAITING_VOICE',mila,true),true);
  assert.equal(advanceAfterAssessment({accepted:true}),'COMPLETE');
  assert.equal(nextEnabled('COMPLETE'),true);
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
  assert.ok(portrait.bottomPadding>=24);assert.ok(landscape.controlFlex>0);
  assert.ok(portrait.visualMaxHeight<640);
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
