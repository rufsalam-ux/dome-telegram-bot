import React,{useEffect,useRef,useState} from 'react';
import {Alert,Animated,Image,Linking,Pressable,ScrollView,Text,View} from 'react-native';
import {RecordingPresets,requestRecordingPermissionsAsync,setAudioModeAsync,useAudioPlayer,useAudioRecorder} from 'expo-audio';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import rawLesson from '../data/botLesson.json';
import {lessonImages} from '../data/lessonImages';
import {API_BASE,completeSession,sendInteractive,sendVoice,startSession,translateText,ttsUrl} from '../api/mobile';
import {useAppStore} from '../store/AppStore';

const lesson:any=rawLesson;const slides:any[]=lesson.slides||[];
const runtimeOrder=(()=>{const by:any={};slides.forEach(x=>by[x.slide_id]=x);const out:any[]=[];let id='slide_01',guard=0;while(id&&by[id]&&guard++<80){out.push(by[id]);id=by[id].next_slide}return out})();
function mediaSource(slide:any){const n=(slide.image||'').split('/').pop();return n&&lessonImages[n]?lessonImages[n]:undefined}
function isVoice(s:any){return ['required_voice','optional_voice'].includes(s.answer_mode)}

export function LessonPlayer(){
 const store=useAppStore();const child=store.selectedChild;
 const[session,setSession]=useState<number|undefined>();const[idx,setIdx]=useState(0);const[attempt,setAttempt]=useState(1);const[recording,setRecording]=useState(false);const[busy,setBusy]=useState(false);const[accepted,setAccepted]=useState(false);const[feedback,setFeedback]=useState('');const[choice,setChoice]=useState('');const[completed,setCompleted]=useState<any>();const[targetText,setTargetText]=useState('');const[nativeText,setNativeText]=useState('');const[voiceBusy,setVoiceBusy]=useState(false);
 const recorder=useAudioRecorder(RecordingPresets.HIGH_QUALITY);const voicePlayer=useAudioPlayer(null);const heroAnim=useRef(new Animated.Value(0)).current;const slide=runtimeOrder[idx];
 const targetLang=child?.learningLanguage||'ru';const nativeLang=child?.nativeLanguage||'ru';

 useEffect(()=>{(async()=>{if(!child)return;try{const r=await startSession(child.id,'demo_001');setSession(r.session_id);setAttempt(r.run_number||1)}catch(e:any){Alert.alert('Урок недоступен',e.message);store.setScreen('home')}})()},[]);

 useEffect(()=>{if(!slide)return;let dead=false;setAccepted(!isVoice(slide));setFeedback('');setChoice('');Animated.loop(Animated.sequence([Animated.timing(heroAnim,{toValue:1,duration:800,useNativeDriver:true}),Animated.timing(heroAnim,{toValue:0,duration:800,useNativeDriver:true})]),{iterations:2}).start();(async()=>{try{const baseTarget=slide.bot_says_target||slide.question||'';const baseNative=slide.bot_explains_native||'';const[t,n]=await Promise.all([translateText(baseTarget,targetLang,'ru'),translateText(baseNative,nativeLang,'ru')]);if(dead)return;setTargetText(t||baseTarget);setNativeText(n||baseNative);await speakPair(baseTarget,baseNative)}catch(e){console.warn('slide voice/translation error',e)}})();return()=>{dead=true}},[idx,targetLang,nativeLang]);

 async function speakPair(baseTarget?:string,baseNative?:string){const a=baseTarget??(slide?.bot_says_target||slide?.question||'');const b=baseNative??(slide?.bot_explains_native||'');if(!a&&!b)return;try{setVoiceBusy(true);voicePlayer.replace({uri:ttsUrl(a,targetLang,b,nativeLang,'ru')});voicePlayer.play()}catch(e:any){console.warn('AI TTS error',e);setFeedback('Голос ведущей временно недоступен.')}finally{setVoiceBusy(false)}}

 async function startRec(){if(!session||busy||recording)return;try{const p=await requestRecordingPermissionsAsync();if(!p.granted){Alert.alert('Нужен доступ к микрофону');return}await setAudioModeAsync({allowsRecording:true,playsInSilentMode:true});await recorder.prepareToRecordAsync();recorder.record();setRecording(true);setAccepted(false);setFeedback('Говори — я тебя слушаю. Когда закончишь, нажми ещё раз.')}catch(e:any){Alert.alert('Микрофон',e.message)}}

 async function stopRec(){if(!recording||!session||busy)return;try{setBusy(true);setFeedback('Слушаю и проверяю ответ…');await recorder.stop();setRecording(false);const uri=recorder.uri;if(!uri)throw new Error('Файл записи не создан');const r=await sendVoice(session,uri,slide.slide_id,slide.required_phrase_id,targetText||slide.question||slide.bot_says_target||'');const ok=String(r.status||'').startsWith('ACCEPTED');const has=Boolean(String(r.transcript||'').trim());setAccepted(ok||has);setFeedback((r.transcript?`Я услышала: «${r.transcript}». `:'')+(r.feedback||r.response_native||r.response_target||(ok||has?'Отлично! Можно идти дальше.':'Попробуй ещё раз.')));if(r.response_target||r.response_native){voicePlayer.replace({uri:ttsUrl(r.response_target||'',targetLang,r.response_native||'',nativeLang,targetLang)});voicePlayer.play()}}catch(e:any){console.error('stopRec error',e);setRecording(false);setAccepted(false);setFeedback('Запись не засчитана: '+e.message)}finally{try{await setAudioModeAsync({allowsRecording:false,playsInSilentMode:true})}catch{}setBusy(false)}}

 async function selectChoice(v:string){setChoice(v);setAccepted(true);if(session)try{await sendInteractive(session,slide.slide_id,slide.kind||'choice',{choice:v})}catch{}}
 const next=async()=>{if(!accepted||busy)return;if(idx<runtimeOrder.length-1){setIdx(x=>x+1);return}if(!session)return;try{setBusy(true);setCompleted(await completeSession(session))}catch(e:any){Alert.alert('Не удалось завершить урок',e.message)}finally{setBusy(false)}};

 if(completed)return <ScrollView contentContainerStyle={{padding:24}}><H1>Урок завершён 🎉</H1><Card><H2>Прохождение {completed.run_number||attempt} из 2</H2><Body>🎬 Мультфильм создан из этого прохождения.</Body>{completed.movie_url?<Button title='▶ Посмотреть мультфильм' onPress={()=>Linking.openURL(completed.movie_url)}/>:<Body muted>Мультфильм обрабатывается на сервере.</Body>}</Card><Button title='Вернуться домой' onPress={()=>store.setScreen('home')}/></ScrollView>;
 if(!slide)return <View style={{padding:24}}><Body>Загрузка урока…</Body></View>;
 const src=mediaSource(slide);const options=slide.mood_options||[];const isCompare=slide.kind==='animal_compare';const isSelector=slide.kind==='card_selector';const isDnD=slide.kind==='drag_and_drop';

 return <ScrollView contentContainerStyle={{padding:18,paddingBottom:42}}><H1>{lesson.title}</H1><Body muted>{idx+1}/{runtimeOrder.length} · прохождение {attempt}/2</Body>{src&&<View style={{position:'relative',borderRadius:18,overflow:'hidden',backgroundColor:'#fff',marginBottom:12}}><Image source={src} style={{width:'100%',height:290,resizeMode:'contain'}}/>{child?.heroUrl&&<Animated.Image source={{uri:child.heroUrl.startsWith('http')?child.heroUrl:API_BASE+child.heroUrl}} style={{position:'absolute',right:8,bottom:4,width:105,height:145,resizeMode:'contain',transform:[{translateY:heroAnim.interpolate({inputRange:[0,1],outputRange:[0,-4]})}]}}/>}</View>}
 <Card>{targetText?<><Body muted>Изучаемый язык ({targetLang})</Body><H2>{targetText}</H2></>:null}{nativeText&&nativeLang!==targetLang?<><Body muted>Объяснение ({nativeLang})</Body><Body>{nativeText}</Body></>:null}<Button secondary disabled={voiceBusy} title='🔊 Повторить голос ведущей' onPress={()=>speakPair()}/>{isVoice(slide)&&<><Button disabled={busy} title={recording?'⏹ Закончить запись':'🎙 Записать ответ'} onPress={recording?stopRec:startRec}/><Body muted>Нажми «Записать ответ», скажи ответ и нажми кнопку ещё раз, чтобы закончить запись.</Body></>}{feedback?<Body>{feedback}</Body>:null}</Card>
 {isSelector&&<Card><H2>Выбери карточку</H2><View style={{flexDirection:'row',flexWrap:'wrap',gap:8}}>{(slide.card_options||['A','Б','В','Г','Д','Е']).map((x:string)=><Button key={x} secondary={choice!==x} title={x} onPress={()=>selectChoice(x)}/>)}</View></Card>}
 {options.length>0&&<Card><H2>Выбери вариант</H2>{options.map((x:string)=><Button key={x} secondary={choice!==x} title={x} onPress={()=>selectChoice(x)}/>)}</Card>}
 {isCompare&&<Card><H2>Сравни животных</H2>{['Больше','Быстрее','Сильнее','Смешнее'].map(x=><Button key={x} secondary={choice!==x} title={x} onPress={()=>selectChoice(x)}/>)}</Card>}
 {isDnD&&<Card><H2>Собери чемодан</H2><Body>Выбери предметы, которые возьмёшь с собой.</Body>{['куртка','шапка','книга','вода'].map(x=><Pressable key={x} onPress={()=>selectChoice(x)} style={{padding:12,borderWidth:1,borderColor:'#ddd',borderRadius:12,marginBottom:8}}><Text>{choice===x?'✓ ':''}{x}</Text></Pressable>)}</Card>}
 <Button disabled={!accepted||busy} title={idx<runtimeOrder.length-1?'Дальше →':'Завершить урок'} onPress={next}/><Button secondary title='🌍 Изменить языки' onPress={()=>store.setScreen('language')}/><Button secondary title='Выйти и сохранить место' onPress={()=>store.setScreen('home')}/></ScrollView>
}
