import assert from 'node:assert/strict';
import test from 'node:test';

import {
  advanceAfterAssessment,
  cardQuestions,
  cardVoiceKey,
  heroBox,
  lessonLayoutPolicy,
  nextCardQuestion,
  nextEnabled,
  recordEnabled,
  runtimePrompt,
  stageAfterTutorSpeech,
} from '../src/engine/lessonRuntime.ts';

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
  assert.equal(stageAfterTutorSpeech(mila,false),'WAITING_INTERACTION');
  assert.equal(stageAfterTutorSpeech(mila,true),'WAITING_VOICE');
  assert.equal(recordEnabled('WAITING_VOICE',mila,true),true);
  assert.equal(advanceAfterAssessment({accepted:true}),'COMPLETE');
  assert.equal(nextEnabled('NEXT'),true);
});

test('third unsupported take advances without being accepted',()=>{
  assert.equal(advanceAfterAssessment({accepted:false,advance_allowed:true,needs_retry:false}),'COMPLETE');
  assert.equal(advanceAfterAssessment({accepted:false,advance_allowed:false,needs_retry:true}),'RETRY');
});

test('Mila hero placement is declarative and left of Mila',()=>{
  assert.deepEqual(heroBox(mila,{default_hero_placement:'hidden'}),[0.04,0.35,0.24,0.61]);
  assert.equal(heroBox({}, {default_hero_placement:'hidden'}),null);
});

test('portrait and landscape keep controls pinned and readable',()=>{
  const portrait=lessonLayoutPolicy(360,640,24);const landscape=lessonLayoutPolicy(800,360,10);
  assert.equal(portrait.landscape,false);assert.equal(landscape.landscape,true);
  assert.equal(portrait.controlsPinned,true);assert.equal(landscape.controlsPinned,true);
  assert.ok(portrait.bottomPadding>=24);assert.ok(landscape.controlFlex>0);
});

test('programmatic demo flow has no early record, duplicate answer, or dead end',()=>{
  let stage=stageAfterTutorSpeech(cards,false);assert.equal(stage,'WAITING_INTERACTION');assert.equal(recordEnabled(stage,cards,false),false);
  stage=stageAfterTutorSpeech(cards,true);assert.equal(stage,'WAITING_VOICE');assert.equal(recordEnabled(stage,cards,true),true);
  stage='PROCESSING';assert.equal(recordEnabled(stage,cards,true),false);
  for(let question=0;question<3;question++){
    const outcome=advanceAfterAssessment({accepted:question!==1,advance_allowed:question===1});assert.equal(outcome,'COMPLETE');
    const next=nextCardQuestion(cards,'A',question);stage=next.done?'NEXT':'WAITING_VOICE';
  }
  assert.equal(stage,'NEXT');assert.equal(nextEnabled(stage),true);
});
