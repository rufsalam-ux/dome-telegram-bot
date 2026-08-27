import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

import {
  adaptiveCardQuestionText,
  adaptiveModelPhrase,
  advanceAfterAssessment,
  answerEnabled,
  cardQuestions,
  cardSelectionAllowed,
  cardVoiceKey,
  childSafeRuntimeMessage,
  completeHelperLanguage,
  computeHeroScale,
  droppedObjectTutorPrompt,
  dropInsideTarget,
  heroBox,
  hasCorrectiveFeedback,
  initialBilingualHint,
  interactionGuidance,
  isRequiredForMovie,
  LessonRuntimeTimeoutError,
  lessonLayoutPolicy,
  movedPixelRect,
  nextCardQuestion,
  nextEnabled,
  progressiveHint,
  recordEnabled,
  renderedPerceptualHeightRatio,
  recordingGate,
  recoveryStageAfterFailure,
  rectanglesOverlap,
  requiresSelection,
  requiresVoice,
  RUNTIME_STAGES,
  runtimePrompt,
  slideContentBoxes,
  stageAfterTutorSpeech,
  suitcaseDropOutcome,
  suitcaseDropAccepted,
  suitcaseTapFallbackAvailable,
  tutorAudioTransition,
  tutorAudioWatchdogStage,
  updatePackedItems,
  visualRequiredForSlide,
  withLessonTimeout,
} from '../src/engine/lessonRuntime.ts';
import {avatarCanvasStyle,avatarFacing,avatarGroundRatio,avatarRenderTrace,avatarScaleX,canonicalChildAvatarUri,lessonAvatarConfig,slideAvatarConfig,sourceAvatarFacing,visibleCharacterAspect,visibleCharacterBox} from '../src/engine/avatarRuntime.ts';
import {CAT_ACTIVITY_STATES,catProcessingState,catStateForStage} from '../src/engine/catRuntime.ts';
import {mediaPhaseAfterEnd,normalizeMediaSequence,usesGenericMediaRuntime} from '../src/engine/mediaRuntime.ts';
import {rootRuntimeFailure,startupFailure,StartupTimeoutError,startupErrorText,withStartupTimeout} from '../src/engine/startup.ts';
import bundledLesson from '../src/data/botLesson.json' with {type:'json'};
import {buildRuntimeOrder} from '../src/data/lessonInteractions.ts';
import {beginVisualAssetLoad,failVisualAsset,loadVisualAssetWithRetry,useLocalizedVisualAsset,visualAssetSourceForKey} from '../src/engine/visualAsset.ts';
import {markPreSlideVideoShown,normalizePreSlideVideo,preSlideVideoKey,preSlideVideoTargetIndex,shouldShowPreSlideVideo} from '../src/engine/preSlideVideo.ts';
import {canonicalTaskType,expectedTargetId,initialPuzzleOrder,isStableTaskTemplate,memoryDeck,moveSequenceItem,puzzleSolved,sequenceSolved,swapPuzzlePieces,taskPairs} from '../src/engine/taskTemplateRuntime.ts';

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

test('PRE_A1 receives one or two complete immediate home-language sentences',()=>{
  assert.equal(initialBilingualHint('Как ты сегодня себя чувствуешь? Потом расскажи подробно.','PRE_A1',0.12),'Как ты сегодня себя чувствуешь? Потом расскажи подробно.');
  assert.equal(initialBilingualHint('Как ты сегодня себя чувствуешь?','A1',0.45),'Как ты сегодня себя чувствуешь?');
  assert.equal(completeHelperLanguage('Кот','Кот поможет тебе. Попробуй ещё раз.'),'Кот поможет тебе. Попробуй ещё раз.');
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
  assert.equal(tutorAudioWatchdogStage('AI_SPEAKING',{playing:false,isBuffering:false},'WAITING_VOICE'),'AI_SPEAKING');
  assert.equal(tutorAudioWatchdogStage('AI_SPEAKING',{playing:false,isBuffering:false},'WAITING_VOICE',true),'WAITING_VOICE');
});

test('transient Android player idle cannot truncate tutor speech mid-sentence',()=>{
  const pending=tutorAudioTransition('AI_SPEAKING',{playing:false,isBuffering:false,didJustFinish:false,currentTime:1.2,duration:8},true,'WAITING_VOICE');
  assert.equal(pending.finished,false);assert.equal(pending.stage,'AI_SPEAKING');
  const done=tutorAudioTransition('AI_SPEAKING',{playing:false,isBuffering:false,didJustFinish:false,currentTime:7.95,duration:8},true,'WAITING_VOICE');
  assert.equal(done.finished,true);assert.equal(done.stage,'WAITING_VOICE');
});

test('recording has no five-second cutoff and stops only after post-speech silence or safety limit',()=>{
  let gate:any={speechStarted:false,silenceStartedAt:null,stopReason:null};
  gate=recordingGate(gate,5_500,-34,5_500);assert.equal(gate.stopReason,null);assert.equal(gate.speechStarted,true);
  gate=recordingGate(gate,6_000,-55,6_000);assert.equal(gate.stopReason,null);
  gate=recordingGate(gate,7_300,-55,7_300);assert.equal(gate.stopReason,'SPEECH_COMPLETE');
  const hard=recordingGate({speechStarted:false,silenceStartedAt:null,stopReason:null},25_000,-80,25_000);assert.equal(hard.stopReason,'SAFETY_LIMIT');
});

test('third unsupported take advances without being accepted',()=>{
  assert.equal(advanceAfterAssessment({accepted:false,advance_allowed:true,needs_retry:false}),'COMPLETE');
  assert.equal(advanceAfterAssessment({accepted:false,advance_allowed:false,needs_retry:true}),'RETRY');
});

test('Mila hero placement is declarative, scene-sized, and left of Mila',()=>{
  const standalone=heroBox(mila,{default_hero_placement:'hidden'})!;
  assert.ok(standalone[3]>=.48&&standalone[3]<=.92);
  assert.equal(heroBox({}, {default_hero_placement:'hidden'}),null);
  const authored=slideAvatarConfig((bundledLesson.slides as any[]).find(slide=>slide.slide_id==='slide_20'),bundledLesson.lesson_id);
  const resolved=heroBox(authored,lessonAvatarConfig(bundledLesson),360,203,{visibleAspectRatio:1}) as [number,number,number,number];
  assert.ok(resolved[3]>=.43,'Mila hero must remain a large scene participant');
  assert.ok(resolved[0]+resolved[2]<Number(authored.character_box[0]));
  assert.ok(Math.abs((resolved[1]+resolved[3])-(authored.character_box[1]+authored.character_box[3]))<.03,'Mila and child must share a baseline');
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
  const opening=slideAvatarConfig((bundledLesson.slides as any[]).find(slide=>slide.slide_id==='slide_01'),bundledLesson.lesson_id);
  assert.equal(opening.image,'lesson-images/slide-01.png');
  const resolved=heroBox(opening,lessonAvatarConfig(bundledLesson),360,203,{visibleAspectRatio:1}) as [number,number,number,number];assert.ok(resolved);
  assert.ok(resolved[3]>=.47);
  for(const box of opening.content_boxes||[])assert.equal(rectanglesOverlap(resolved,box),false);
});

test('runtime has the authored conversation state machine and follow-up transition',()=>{
  assert.deepEqual(RUNTIME_STAGES,['ENTER','AI_SPEAKING','WAITING_ACTION','WAITING_VOICE','PROCESSING','FEEDBACK','FOLLOW_UP','RETRY','COMPLETE']);
  assert.equal(advanceAfterAssessment({accepted:true,tutor_turn:{follow_up_target:'Why?'}}),'FOLLOW_UP');
});

test('hero collision falls back and drag hit testing uses real target bounds',()=>{
  const slide={hero_anchor:'left',content_boxes:[[0,0.3,0.3,0.7]],hero_fallback_anchors:['right']};
  const fallback=heroBox(slide,{default_hero_placement:'hidden'})!;assert.ok(fallback[0]>.5&&fallback[3]>.62);
  assert.equal(rectanglesOverlap([0,0,0.2,0.2],[0.1,0.1,0.2,0.2]),true);
  assert.equal(dropInsideTarget(150,130,{x:100,y:100,width:100,height:60}),true);
  assert.equal(dropInsideTarget(40,40,{x:100,y:100,width:100,height:60}),false);
  assert.equal(suitcaseDropOutcome(false,true),'PACK');
  assert.equal(suitcaseDropOutcome(true,false),'UNPACK');
  assert.equal(suitcaseDropOutcome(false,false),'RETURN');
});

test('hero scale has a visual-height floor and remains bounded',()=>{
  const scale=computeHeroScale(360,203,[0.8,0.52,0.16,0.44]);
  assert.ok(scale>=2&&scale<=3);
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
  assert.equal(droppedObjectTutorPrompt('Camera','What will you take?'),'Camera! What will you take?');
  assert.equal(nextEnabled('WAITING_VOICE',true,{requiredForMovie:false}),true);
});

test('generic media supports an authored intro-video to image sequence',()=>{
  const slide={media_sequence:[{id:'intro',type:'video',src:'video/intro.mp4',autoplay:true,advance_on_end:true},{id:'scene',type:'image',src:'images/scene.png'}]};
  const sequence=normalizeMediaSequence(slide);assert.deepEqual(sequence.map(item=>item.type),['video','image']);
  assert.equal(usesGenericMediaRuntime(slide),true);assert.equal(mediaPhaseAfterEnd(sequence,0),1);assert.equal(mediaPhaseAfterEnd(sequence,1),1);
});

test('DOME cat is an independent companion and reward star stays in its own layer',()=>{
  assert.deepEqual(CAT_ACTIVITY_STATES,['idle','listening','thinking','happy','encouraging','surprised','waiting','playing','sleeping']);
  assert.equal(catStateForStage('AI_SPEAKING'),'listening');assert.equal(catStateForStage('WAITING_VOICE'),'waiting');
  assert.equal(catProcessingState(1499),'thinking');assert.equal(catProcessingState(1500),'idle');assert.equal(catProcessingState(4000),'waiting');
  const cat=readFileSync(new URL('../src/components/CatActivityLayer.tsx',import.meta.url),'utf8');const reward=readFileSync(new URL('../src/components/RewardEffectLayer.tsx',import.meta.url),'utf8');const player=readFileSync(new URL('../src/screens/LessonPlayer.tsx',import.meta.url),'utf8');
  assert.doesNotMatch(cat,/star\.png|gameActive|cat-mini-game-star|assets\/heroes\/cat\.png/);assert.match(cat,/dome-splash-v2\.png/);assert.match(cat,/const focused=stage==='AI_SPEAKING'\|\|stage==='PROCESSING'/);assert.doesNotMatch(cat,/return null/);assert.match(reward,/star\.png/);assert.match(player,/<CatActivityLayer/);
  assert.match(player,/droppedObjectTutorPrompt\(labelTarget,targetText\)/);
});

test('regression: selected child avatar identity persists across slide transitions',()=>{
  const child={id:7,heroUrl:'/media/children/7/canonical.png'};
  const first=canonicalChildAvatarUri(child,'https://api.dome.test');
  const second=canonicalChildAvatarUri({...child},'https://api.dome.test');
  assert.equal(first,'https://api.dome.test/media/children/7/canonical.png');assert.equal(second,first);
  for(const slide of (bundledLesson.slides as any[]).slice(0,6))assert.ok(slideAvatarConfig(slide,bundledLesson.lesson_id));
});

test('head-left dinosaur is mirrored only when a scene requires facing right',()=>{
  const metadata={facingDirection:'LEFT',characterBoundingBox:[.04,.08,.9,.86]};
  assert.equal(sourceAvatarFacing(metadata),'LEFT');
  assert.equal(avatarScaleX('left',sourceAvatarFacing(metadata)),1);
  assert.equal(avatarScaleX('right',sourceAvatarFacing(metadata)),-1);
  assert.equal(avatarScaleX('front',sourceAvatarFacing(metadata)),1);
  assert.deepEqual(visibleCharacterBox(metadata),[.04,.08,.9,.86]);
});

test('regression: AI state changes cannot replace or duplicate the child avatar',()=>{
  const avatar=readFileSync(new URL('../src/components/ChildAvatarLayer.tsx',import.meta.url),'utf8');
  const player=readFileSync(new URL('../src/screens/LessonPlayer.tsx',import.meta.url),'utf8');
  assert.doesNotMatch(avatar,/RuntimeStage|AI_SPEAKING|PROCESSING|star\.png|cat\.png/);
  assert.equal((player.match(/<ChildAvatarLayer/g)||[]).length,2,'one authored image path plus one generic-media path');
  assert.match(player,/canonicalChildAvatarUri\(child,API_BASE\)/);
});

test('regression: reward star can never become the child avatar or cat',()=>{
  const avatar=readFileSync(new URL('../src/components/ChildAvatarLayer.tsx',import.meta.url),'utf8');
  const cat=readFileSync(new URL('../src/components/CatActivityLayer.tsx',import.meta.url),'utf8');
  const reward=readFileSync(new URL('../src/components/RewardEffectLayer.tsx',import.meta.url),'utf8');
  assert.doesNotMatch(avatar+cat,/star\.png/);assert.match(reward,/star\.png/);assert.match(reward,/pointerEvents='none'/);
});

test('regression: card selection preserves the current visual slide',()=>{
  const player=readFileSync(new URL('../src/screens/LessonPlayer.tsx',import.meta.url),'utf8');
  assert.match(player,/const artworkSlide=slide/);assert.doesNotMatch(player,/selectedCardBranch/);
  assert.match(player,/Legacy slide_10\.\.15 artwork/);
});

test('regression: card questions advance conversationally without changing media',()=>{
  const questions=cardQuestions(cards,'A');let index=0;const visual='lesson-images/slide-09.png';
  for(const expected of ['A2','A3']){const result=nextCardQuestion(cards,'A',index);assert.equal(result.question?.id,expected);assert.equal(visual,'lesson-images/slide-09.png');index=result.index}
  assert.equal(nextCardQuestion(cards,'A',index).done,true);
});

test('studio-authored lessons use data order without demo_001 slide ids',()=>{
  const slides=[{slide_id:'welcome',order:2},{slide_id:'warmup',order:1},{slide_id:'finish',order:3}];
  assert.deepEqual(buildRuntimeOrder(slides).map(slide=>slide.slide_id),['warmup','welcome','finish']);
});

test('regression: optional tasks expose Next during speech, processing, and voice wait',()=>{
  const optional={answer_mode:'required_voice'};assert.equal(isRequiredForMovie(optional),false);
  for(const stage of ['AI_SPEAKING','PROCESSING','WAITING_VOICE'] as const)assert.equal(nextEnabled(stage,true,{requiredForMovie:isRequiredForMovie(optional)}),true);
});

test('regression: requiredForMovie cannot be skipped by exhausting retries',()=>{
  const required={answer_mode:'required_voice',requiredForMovie:true};assert.equal(isRequiredForMovie(required),true);
  assert.equal(nextEnabled('WAITING_VOICE',true,{requiredForMovie:true}),false);
  assert.equal(nextEnabled('WAITING_VOICE',true,{requiredForMovie:true,hasValidRecording:true}),true);
  assert.equal(nextEnabled('COMPLETE',true,{requiredForMovie:true}),true);
  assert.equal(nextEnabled('RETRY',true,{requiredForMovie:true,recoveryAvailable:true}),false);
  assert.equal(isRequiredForMovie({requiredForMovie:'true'}),false);
  const player=readFileSync(new URL('../src/screens/LessonPlayer.tsx',import.meta.url),'utf8');
  assert.doesNotMatch(player,/Продолжить с примером|required_recovery/);
});

test('regression: failed AI/backend work has a bounded timeout and a deterministic recovery stage',async()=>{
  await assert.rejects(withLessonTimeout(new Promise(()=>{}),'voice assessment',5),error=>error instanceof LessonRuntimeTimeoutError&&error.operation==='voice assessment');
  assert.equal(recoveryStageAfterFailure(greeting,true),'WAITING_VOICE');
  assert.equal(tutorAudioWatchdogStage('AI_SPEAKING',{playing:true,isBuffering:true},'WAITING_VOICE',true),'WAITING_VOICE');
  for(const operation of ['recording','answer','completion'] as const){const message=childSafeRuntimeMessage(operation);assert.doesNotMatch(message,/timeout|HTTP|FFmpeg|Railway|exit code/i)}
});

test('regression: Mila selection, voice, feedback, and Next all have an exit',()=>{
  let stage=stageAfterTutorSpeech(mila,false);assert.equal(stage,'WAITING_ACTION');
  stage=stageAfterTutorSpeech(mila,true);assert.equal(stage,'WAITING_VOICE');
  stage=advanceAfterAssessment({accepted:true});assert.equal(stage,'COMPLETE');
  assert.equal(nextEnabled(stage,true,{requiredForMovie:isRequiredForMovie(mila)}),true);
  assert.equal(recoveryStageAfterFailure(mila,true),'WAITING_VOICE');
});

test('regression: avatar sizing and orientation are scene-relative for Lyosha and Mila',()=>{
  const lesson=lessonAvatarConfig(bundledLesson);const lyosha=slideAvatarConfig((bundledLesson.slides as any[]).find(slide=>slide.slide_id==='slide_19'),bundledLesson.lesson_id);const milaSlide=slideAvatarConfig((bundledLesson.slides as any[]).find(slide=>slide.slide_id==='slide_20'),bundledLesson.lesson_id);
  const geometry={facingDirection:'LEFT',visibleAspectRatio:1};const lyoshaBox=heroBox(lyosha,lesson,360,203,geometry) as number[];const milaBox=heroBox(milaSlide,lesson,360,203,geometry) as number[];
  assert.ok(lyoshaBox[3]>=.43);assert.ok(lyoshaBox[0]+lyoshaBox[2]<lyosha.character_box[0]);assert.ok(Math.abs((lyoshaBox[1]+lyoshaBox[3])-(lyosha.character_box[1]+lyosha.character_box[3]))<.03);assert.equal(avatarFacing(lyosha,lesson),'right');assert.equal(avatarScaleX(avatarFacing(lyosha,lesson),sourceAvatarFacing(geometry)),-1);
  assert.ok(milaBox[3]>=.43);assert.ok(milaBox[0]+milaBox[2]<milaSlide.character_box[0]);assert.equal(avatarFacing(milaSlide,lesson),'right');assert.equal(avatarScaleX(avatarFacing(milaSlide,lesson),sourceAvatarFacing(geometry)),-1);
});

test('wide dinosaur is visibly large, remains left of Lyosha/Mila, and confirmed head side wins',()=>{
  const lesson=lessonAvatarConfig(bundledLesson);const geometry={userConfirmed:true,canonicalFacing:'LEFT',facingDirection:'RIGHT',visibleAspectRatio:1.85,analysisVersion:'character-geometry-v3'};
  for(const [slideId,minimum] of [['slide_19',.75],['slide_20',.75]] as const){const slide=slideAvatarConfig((bundledLesson.slides as any[]).find(item=>item.slide_id===slideId),bundledLesson.lesson_id);const box=heroBox(slide,lesson,360,203,geometry) as [number,number,number,number];const partner=slide.character_box as [number,number,number,number];assert.ok(box);assert.ok(box[0]+box[2]+.01<partner[0],`${slideId} must keep a visible left gap`);assert.ok(renderedPerceptualHeightRatio(box,partner,geometry.visibleAspectRatio)>=minimum,`${slideId} rendered silhouette is still too small`);assert.ok(Math.abs((box[1]+box[3])-(partner[1]+partner[3]))<.035,`${slideId} ground lines differ`);const trace=avatarRenderTrace(geometry,'right');assert.equal(trace.sourceFacing,'LEFT');assert.equal(trace.appliedFlip,true);assert.equal(trace.displayedFacing,'RIGHT')}
  assert.equal(sourceAvatarFacing({userConfirmed:true,canonicalFacing:'UNKNOWN',facingDirection:'LEFT'}),'LEFT');
});

test('correction finishes TTS before Answer and preserves the correct Next policy',()=>{
  const correction={accepted:false,advance_allowed:true,correction_target:'I have a blue book.'};
  assert.equal(hasCorrectiveFeedback(correction),true);
  assert.equal(recordEnabled('AI_SPEAKING',greeting,true),false);
  assert.equal(recordEnabled('WAITING_VOICE',greeting,true),true);
  for(const postTts of ['WAITING_VOICE','FEEDBACK','RETRY','FOLLOW_UP'] as const)assert.equal(answerEnabled(postTts,greeting,true,false,false),true);
  assert.equal(answerEnabled('AI_SPEAKING',greeting,true,false,false),false);assert.equal(answerEnabled('WAITING_VOICE',greeting,true,true,false),false);
  assert.equal(nextEnabled('WAITING_VOICE',true,{requiredForMovie:false}),true);
  assert.equal(nextEnabled('WAITING_VOICE',true,{requiredForMovie:true,hasValidRecording:false}),false);
  assert.equal(nextEnabled('WAITING_VOICE',true,{requiredForMovie:true,hasValidRecording:true}),true);
});

test('all 27 demo slides declare avatar visibility and visible placements avoid protected content',()=>{
  const order=buildRuntimeOrder(bundledLesson.slides as any[]);const lesson=lessonAvatarConfig(bundledLesson);const geometry={visibleAspectRatio:1,facingDirection:'LEFT'};
  assert.equal(order.length,27);
  for(const raw of order){
    const slide=slideAvatarConfig(raw,bundledLesson.lesson_id);assert.ok(['scene','hidden'].includes(String(slide.hero_visibility)),`${slide.slide_id} needs an explicit avatar visibility rule`);
    const box=heroBox(slide,lesson,360,203,geometry);
    if(slide.hero_visibility==='hidden'){assert.equal(box,null,`${slide.slide_id} must hide the hero`);continue}
    assert.ok(box,`${slide.slide_id} needs a collision-free hero box`);
    for(const protectedBox of slideContentBoxes(slide))assert.equal(rectanglesOverlap(box as any,protectedBox),false,`${slide.slide_id} hero overlaps protected content`);
  }
});

test('confirmed geometry drives the same perceptual canvas and ground anchor',()=>{
  const geometry={characterBoundingBox:[.1,.08,.8,.84],sourceWidth:500,sourceHeight:800,visibleAspectRatio:.595,feetAnchor:[.5,.9]};
  assert.equal(visibleCharacterAspect(geometry),.595);assert.ok(avatarGroundRatio(geometry)>.95);
  const style=avatarCanvasStyle(geometry);assert.equal(style.position,'absolute');assert.match(style.left,/^-/);assert.match(style.width,/%$/);
  const confirmation=readFileSync(new URL('../src/screens/HeroConfirmScreen.tsx',import.meta.url),'utf8');
  for(const marker of ['ГОЛОВА','ПЕРЕД','ЗАД','ЛЕВАЯ ЛАПА','ПРАВАЯ ЛАПА','НОГИ / ОПОРА'])assert.match(confirmation,new RegExp(marker));
  assert.match(confirmation,/PanResponder\.create/);assert.match(confirmation,/confirmHeroGeometry/);
});

test('My Hero flow is React Native safe and never calls browser reload APIs',()=>{
  const hero=readFileSync(new URL('../src/screens/HeroScreen.tsx',import.meta.url),'utf8');const confirmation=readFileSync(new URL('../src/screens/HeroConfirmScreen.tsx',import.meta.url),'utf8');
  const sources=['../App.tsx','../index.js','../src/screens/RootApp.tsx','../src/store/AppStore.tsx'].map(path=>readFileSync(new URL(path,import.meta.url),'utf8')).join('\n')+hero+confirmation;
  assert.doesNotMatch(sources,/\bwindow\s*\.|\bdocument\s*\.|\blocation\s*\.\s*reload|\.\s*reload\s*\(/);assert.match(sources,/setScreen\('hero_confirm'\)/);assert.match(sources,/updateChild/);assert.match(sources,/setScreen\('home'\)/);
  const presetBody=hero.slice(hero.indexOf('const preset='),hero.indexOf('const upload='));const uploadBody=hero.slice(hero.indexOf('const upload='));assert.doesNotMatch(presetBody,/hero_confirm/);assert.match(presetBody,/setScreen\('home'\)/);assert.match(uploadBody,/setScreen\('hero_confirm'\)/);
  assert.match(hero,/custom-avatar-processing/);assert.match(hero,/Загружаем и распознаём твоего героя/);assert.match(hero,/ActivityIndicator/);assert.match(hero,/allowsEditing:false/);
  for(const marker of ['leftHandOrFrontPaw','rightHandOrFrontPaw','leftLegOrRearLimb','rightLegOrRearLimb'])assert.match(confirmation,new RegExp(marker));
  assert.match(sources,/EXPO_PUBLIC_BUILD_COMMIT/);assert.match(sources,/dome-build-marker/);
});

test('interaction waits explain the physical action and provide a highlighted target layer',()=>{
  assert.match(interactionGuidance(mila),/подарок.*нажми/i);assert.match(interactionGuidance({interactive_task:'suitcase'}),/Перетащи/i);
  const player=readFileSync(new URL('../src/screens/LessonPlayer.tsx',import.meta.url),'utf8');assert.match(player,/interaction-guidance/);assert.match(player,/interactionAttention/);
});

test('parrot remains movie-required and receives an adaptive natural target model',()=>{
  const parrot=(bundledLesson.slides as any[]).find(slide=>slide.slide_id==='slide_44');assert.equal(parrot.requiredForMovie,true);assert.equal(isRequiredForMovie(parrot),true);assert.equal(nextEnabled('WAITING_VOICE',true,{requiredForMovie:true,hasValidRecording:false}),false);assert.equal(answerEnabled('RETRY',parrot,true,false,false),true);assert.match(adaptiveModelPhrase(parrot,'PRE_A1',.15),/Попугай.+красный.+красивый/);assert.match(adaptiveModelPhrase(parrot,'A2',.6),/живёт в тёплом месте/);
});

test('pre-slide video is a presentation hook and never changes the 27-step order',()=>{
  const order=buildRuntimeOrder(bundledLesson.slides as any[]);const slides=order.map(item=>({...item}));slides[10]={...slides[10],preSlideVideo:{enabled:true,uri:'media/intro-11.mp4',skippable:true,showPolicy:'once_ever',autoplay:true}};
  const state={attempt:[],ever:[]};const transition=preSlideVideoTargetIndex(9,slides,state);assert.equal(slides.length,27);assert.equal(transition.nextIndex,10);assert.equal(transition.video?.uri,'media/intro-11.mp4');assert.equal(shouldShowPreSlideVideo(slides[10],state),true);
  const key=preSlideVideoKey(slides[10],normalizePreSlideVideo(slides[10])!);const shown=markPreSlideVideoShown(state,key);assert.equal(shouldShowPreSlideVideo(slides[10],shown),false);assert.equal(preSlideVideoTargetIndex(9,slides,shown).video,null);
  assert.equal(normalizePreSlideVideo({preSlideVideo:{enabled:true,uri:''}}),null);const component=readFileSync(new URL('../src/components/PreSlideVideoStage.tsx',import.meta.url),'utf8');const api=readFileSync(new URL('../src/api/mobile.ts',import.meta.url),'utf8');assert.match(component,/contentFit='contain'/);assert.match(component,/finish\('failed'\)/);assert.match(component,/lessonMediaSource/);assert.ok((api.match(/useCaching:true/g)||[]).length>=2);
});

test('regression: cat state remains independent from child avatar identity',()=>{
  const uri=canonicalChildAvatarUri({heroUrl:'https://cdn.test/child.png'},'https://api.test');
  for(const stage of RUNTIME_STAGES){assert.equal(canonicalChildAvatarUri({heroUrl:uri},'https://api.test'),uri);assert.ok(CAT_ACTIVITY_STATES.includes(catStateForStage(stage)))}
});

test('progressive assistance advances from rephrase to example, starter, and choices',()=>{
  const slide={question:'Какой должен быть друг?',simplified_text:'Мой друг должен быть добрым.',selection_options:[{label:'Добрый'},{label:'Весёлый'}]};
  assert.equal(progressiveHint(slide,1).step,'REPHRASE');assert.equal(progressiveHint(slide,2).step,'EXAMPLE');assert.equal(progressiveHint(slide,3).step,'STARTER');assert.equal(progressiveHint(slide,4).step,'CHOICES');assert.match(progressiveHint(slide,4).prompt,/Добрый.*Весёлый/);
});

test('cold startup is isolated from lesson native modules and cannot wait forever',async()=>{
  const packageJson=JSON.parse(readFileSync(new URL('../package.json',import.meta.url),'utf8'));
  const entry=readFileSync(new URL('../index.js',import.meta.url),'utf8');
  const app=readFileSync(new URL('../App.tsx',import.meta.url),'utf8');
  const root=readFileSync(new URL('../src/screens/RootApp.tsx',import.meta.url),'utf8');
  const mobileApi=readFileSync(new URL('../src/api/mobile.ts',import.meta.url),'utf8');
  const ui=readFileSync(new URL('../src/components/Ui.tsx',import.meta.url),'utf8');
  assert.equal(packageJson.main,'index.js');assert.match(entry,/ENTRY_EVALUATION/);assert.match(entry,/registerRootComponent/);assert.match(entry,/APP_MODULE_LOAD_FAILED/);
  assert.doesNotMatch(app,/from ['"]\.\/src\/store\/AppStore['"]/);assert.doesNotMatch(app,/from ['"]\.\/src\/screens\/RootApp['"]/);assert.match(app,/import \{AppRuntime\} from ['"]\.\/src\/AppRuntime['"]/);
  assert.match(mobileApi,/import \* as SecureStore from ['"]expo-secure-store['"]/);assert.match(mobileApi,/require\(['"]expo-file-system\/legacy['"]\)/);
  assert.doesNotMatch(root,/import\s+\{LessonPlayer\}\s+from/);assert.match(root,/require\(['"]\.\/LessonPlayer['"]\)/);assert.match(root,/import \{AuthScreen\} from ['"]\.\/AuthScreen['"]/);assert.match(root,/withStartupTimeout/);
  assert.doesNotMatch(ui,/import\s+\{?\s*useAudioPlayer/);assert.match(ui,/require\(['"]expo-audio['"]\)/);assert.ok(ui.indexOf('onPress();')<ui.indexOf('Vibration.vibrate(8)'));assert.match(ui,/DOME_TAP_HAPTIC_UNAVAILABLE/);
  assert.doesNotMatch(app+root+mobileApi+ui,/React\.lazy|\bimport\s*\(/);
  assert.match(app,/key=\{runtimeAttempt\}/);assert.match(app,/setRuntimeAttempt\(value=>value\+1\)/);assert.match(app,/fatal-boot-error/);
  assert.match(app,/RETRY_PRESS_RECEIVED/);assert.match(app,/BOOT STAGE:/);assert.match(app,/BOOT ERROR:/);assert.match(app,/RETRY COUNT:/);assert.match(app,/disabled=\{false\}/);assert.match(app,/onPress=\{this\.props\.onRetryPress\}/);
  assert.ok(app.indexOf('<RootErrorBoundary')<app.indexOf('<SafeAreaProvider>'));assert.doesNotMatch(app,/<Button\b/);
  for(const stage of ['APP_MOUNT','STORE_RESTORE','SESSION_RESTORE','BACKEND_HEALTH','PROFILE_LOAD','NAVIGATION_READY','APP_READY','FIRST_SCREEN_RENDERED'])assert.match(app+root,new RegExp(`['"]${stage}['"]`));
  await assert.rejects(withStartupTimeout(new Promise(()=>{}),'bootstrap',5),error=>error instanceof StartupTimeoutError&&error.stage==='bootstrap');
  assert.match(startupErrorText(new StartupTimeoutError('secure_store',5)),/сохранённый вход/);
  assert.equal(rootRuntimeFailure(new Error('Cannot construct ExpoAudio AudioPlayer shared object')).code,'UI_AUDIO_INIT');
  assert.equal(startupFailure(new StartupTimeoutError('bootstrap',5)).code,'BOOTSTRAP_TIMEOUT');
  assert.equal(startupFailure(new TypeError('Network request failed')).code,'BACKEND_NETWORK');
});

test('normal root replaces the temporary touch diagnostic and retries bootstrap in process',()=>{
  const entry=readFileSync(new URL('../index.js',import.meta.url),'utf8');
  const app=readFileSync(new URL('../App.tsx',import.meta.url),'utf8');
  const root=readFileSync(new URL('../src/screens/RootApp.tsx',import.meta.url),'utf8');
  const metro=readFileSync(new URL('../metro.config.js',import.meta.url),'utf8');
  assert.doesNotMatch(entry,/RootTouchDiagnostic|EXPO_PUBLIC_DOME_TOUCH_DIAGNOSTIC/);
  assert.match(entry,/require\(['"]\.\/App['"]\)/);
  assert.match(root,/setBootAttempt\(value=>value\+1\)/);assert.match(root,/setStartupError\(null\);setSessionReady\(false\)/);
  assert.match(root,/bootstrap-retry-button/);assert.doesNotMatch(root,/window\.|document\.|location\.reload/);
  assert.doesNotMatch(app,/window\.|document\.|location\.reload/);assert.match(app,/failure\.stack/);assert.match(app,/failingLocation/);assert.match(app,/failingFunction/);
  assert.match(metro,/__dome_startup/);assert.match(metro,/DOME_DEVICE_BEACON/);assert.match(metro,/statusCode=204/);
  const error=new Error('Synthetic bootstrap crash');error.stack='Error: Synthetic bootstrap crash\n    at restoreSession (src/store/AppStore.tsx:88:12)';
  const failure=rootRuntimeFailure(error,'STORE_RESTORE');assert.equal(failure.stage,'STORE_RESTORE');assert.equal(failure.failingFunction,'restoreSession');assert.match(failure.failingLocation,/AppStore\.tsx:88:12/);
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

test('stable authored task templates are deterministic and data-only',()=>{
  const matching={slide_id:'match_1',type:'matching',pairs:[{id:'cat',left:'🐱',right:'cat'},{id:'dog',left:'🐶',right:'dog'}]};
  assert.equal(isStableTaskTemplate(matching),true);assert.equal(taskPairs(matching).length,2);assert.equal(requiresSelection(matching),true);
  const memory={...matching,type:'memory'};assert.deepEqual(memoryDeck(memory),memoryDeck(memory));assert.equal(memoryDeck(memory).length,4);
  const drag={type:'drag_drop',items:[{id:'coat',target_id:'bag'}],targets:[{id:'bag'}]};assert.equal(expectedTargetId(drag,'coat',0),'bag');assert.equal(requiresSelection(drag),true);
  assert.equal(canonicalTaskType({type:'ordering'}),'sequence');assert.equal(isStableTaskTemplate({type:'ordering'}),true);
});

test('puzzle and sequence templates require a real completed action',()=>{
  const order=initialPuzzleOrder(6,'puzzle_1');assert.equal(order.length,6);assert.equal(puzzleSolved(order),false);
  let current=order;for(let expected=0;expected<current.length;expected++){const found=current.indexOf(expected);current=swapPuzzlePieces(current,expected,found)}assert.equal(puzzleSolved(current),true);
  assert.deepEqual(moveSequenceItem(['b','a','c'],0,1),['a','b','c']);assert.equal(sequenceSolved(['a','b','c'],['a','b','c']),true);
  assert.equal(stageAfterTutorSpeech({type:'puzzle',pieces:6},false),'WAITING_ACTION');
});

test('disabled Content Studio steps never become child progress steps',()=>{
  const order=buildRuntimeOrder([{slide_id:'one',order:1},{slide_id:'disabled',order:2,enabled:false},{slide_id:'three',order:3}]);
  assert.deepEqual(order.map(step=>step.slide_id),['one','three']);
});
