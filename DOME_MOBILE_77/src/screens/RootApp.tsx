import React,{Suspense,useEffect,useRef,useState} from 'react';
import {Alert,Image,Linking,Pressable,ScrollView,Share,Text,View} from 'react-native';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {useAppStore} from '../store/AppStore';
import {bootstrap,isUnauthorizedError,listLessons,listMovies,API_BASE,restoreApiToken,updateChildLanguages} from '../api/mobile';
import {BootStage,logStartupStage,startupFailure,StartupFailure,withStartupTimeout} from '../engine/startup';

// LessonPlayer initializes native audio/video modules. Load it only when the
// lesson route is requested so a media-module problem can never block app boot.
const LazyLessonPlayer=React.lazy(()=>import('./LessonPlayer').then(module=>({default:module.LessonPlayer})));
const LazyPurchaseScreen=React.lazy(()=>import('./PurchaseScreen').then(module=>({default:module.PurchaseScreen})));
const LazyAdminScreen=React.lazy(()=>import('./AdminScreen').then(module=>({default:module.AdminScreen})));
const LazyAuthScreen=React.lazy(()=>import('./AuthScreen').then(module=>({default:module.AuthScreen})));
const LazyHeroScreen=React.lazy(()=>import('./HeroScreen').then(module=>({default:module.HeroScreen})));
const LazyHeroConfirmScreen=React.lazy(()=>import('./HeroConfirmScreen').then(module=>({default:module.HeroConfirmScreen})));
const LazyAddChildScreen=React.lazy(()=>import('./AddChildScreen').then(module=>({default:module.AddChildScreen})));

const LANGUAGES=[
  ['ru','Русский'],['en','English'],['es','Español'],['de','Deutsch'],['fr','Français'],
  ['it','Italiano'],['pt','Português'],['tr','Türkçe'],['ar','العربية'],['zh','中文']
] as const;

type RootAppProps={
  onBootStage:(stage:BootStage,failure?:StartupFailure|null)=>void;
  retryCount:number;
  onRetryReceived:()=>number;
};

export function RootApp({onBootStage,retryCount,onRetryReceived}:RootAppProps){
  const s=useAppStore();
  const[movies,setMovies]=useState<any[]>([]);
  const[target,setTarget]=useState('ru');
  const[native,setNative]=useState('ru');
  const[savingLang,setSavingLang]=useState(false);
  const[sessionReady,setSessionReady]=useState(false);
  const[startupError,setStartupError]=useState<StartupFailure|null>(null);
  const[bootAttempt,setBootAttempt]=useState(0);
  const[lessons,setLessons]=useState<any[]>([]);
  const[lessonsLoading,setLessonsLoading]=useState(false);
  const[lessonsError,setLessonsError]=useState('');
  const[activeLessonId,setActiveLessonId]=useState('');
  const[catalogReloadNonce,setCatalogReloadNonce]=useState(0);
  const firstScreenLoggedForAttempt=useRef(-1);
  const visibleChildren=s.children.filter(child=>Boolean(child?.id&&child?.name?.trim()));

  useEffect(()=>{
    let active=true;
    setSessionReady(false);setStartupError(null);
    (async()=>{
      let route='auth';
      let failingStage:BootStage='STORE_RESTORE';
      let failed=false;
      const reach=(stage:BootStage)=>{failingStage=stage;if(active)onBootStage(stage)};
      try{
        reach('STORE_RESTORE');
        reach('SESSION_RESTORE');
        const token=await withStartupTimeout(restoreApiToken(),'secure_store',5000);
        logStartupStage('SECURESTORE_DONE',{has_token:Boolean(token),boot_attempt:bootAttempt});
        if(!token){
          logStartupStage('BACKEND_BOOTSTRAP_DONE',{skipped:true,reason:'NO_TOKEN',boot_attempt:bootAttempt});
          return;
        }
        reach('BACKEND_HEALTH');
        const data=await withStartupTimeout(bootstrap(),'bootstrap',8000);
        logStartupStage('BACKEND_BOOTSTRAP_DONE',{child_count:Array.isArray(data?.children)?data.children.length:0,boot_attempt:bootAttempt});
        reach('PROFILE_LOAD');
        if(active)await withStartupTimeout(s.hydrate(data,token),'hydrate',5000);
        route='children';
      }catch(error:any){
        console.error('DOME_STARTUP_RESTORE_ERROR',{stage:failingStage,name:error?.name,message:error?.message,stack:error?.stack},error);
        if(active&&isUnauthorizedError(error))await withStartupTimeout(s.logout(),'logout',3000).catch(logoutError=>console.error('DOME_STARTUP_LOGOUT_ERROR',logoutError));
        else if(active){
          failed=true;route='startup_error';const failure=startupFailure(error,failingStage);
          console.error('DOME_BOOTSTRAP_FAILED',JSON.stringify(failure),error);
          onBootStage(failingStage,failure);setStartupError(failure);
        }
      }finally{
        if(active){
          if(!failed){onBootStage('NAVIGATION_READY');logStartupStage('NAV_READY',{route,boot_attempt:bootAttempt})}
          setSessionReady(true);
        }
      }
    })();
    return()=>{active=false};
  },[bootAttempt,onBootStage,s.hydrate,s.logout]);
  useEffect(()=>{
    if(!sessionReady||startupError||firstScreenLoggedForAttempt.current===bootAttempt)return;
    const frame=requestAnimationFrame(()=>{
      firstScreenLoggedForAttempt.current=bootAttempt;
      onBootStage('APP_READY');
      logStartupStage('FIRST_SCREEN_RENDERED',{screen:s.screen,boot_attempt:bootAttempt});
    });
    return()=>cancelAnimationFrame(frame);
  },[bootAttempt,onBootStage,sessionReady,startupError,s.screen]);
  useEffect(()=>{if(s.selectedChild){setTarget(s.selectedChild.learningLanguage||'ru');setNative(s.selectedChild.nativeLanguage||'ru')}},[s.selectedChild?.id]);
  useEffect(()=>{let active=true;const child=s.selectedChild;if(!child){setLessons([]);setActiveLessonId('');return}setLessonsLoading(true);setLessonsError('');void listLessons(child.id).then(data=>{if(!active)return;const items=Array.isArray(data?.lessons)?data.lessons:[];setLessons(items);const first=items.find((item:any)=>item.available&&item.resume_step!==null)||items.find((item:any)=>item.available);setActiveLessonId(current=>items.some((item:any)=>item.lesson_id===current&&item.available)?current:String(first?.lesson_id||''))}).catch(error=>{if(active&&!isUnauthorizedError(error)){setLessons([]);setActiveLessonId('');setLessonsError(error.message||'Не удалось загрузить уроки')}}).finally(()=>{if(active)setLessonsLoading(false)});return()=>{active=false}},[s.selectedChild?.id,catalogReloadNonce]);
  const openLesson=(lessonId:string)=>{setActiveLessonId(lessonId);s.setScreen('lesson')};
  const activeLesson=lessons.find(item=>item.lesson_id===activeLessonId&&item.available)||lessons.find(item=>item.available);

  if(!sessionReady)return <View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Body>Восстанавливаем вход…</Body></View>;
  if(startupError){
    const retry=()=>{
      const next=onRetryReceived();
      console.log('DOME_BOOTSTRAP_RETRY',{retry_count:next,failed_stage:startupError.stage,boot_attempt:bootAttempt});
      setStartupError(null);setSessionReady(false);onBootStage('STORE_RESTORE');setBootAttempt(value=>value+1);
    };
    return <ScrollView testID='bootstrap-failure-screen' contentContainerStyle={{flexGrow:1,justifyContent:'center',padding:28}} keyboardShouldPersistTaps='always'>
      <Text style={{fontSize:34,fontWeight:'900',color:'#20243A'}}>DOME</Text>
      <Text style={{fontSize:16,marginVertical:12,color:'#42475C'}}>{startupError.message} Проверьте интернет и повторите попытку.</Text>
      <Text testID='bootstrap-boot-stage' selectable style={{fontSize:12,marginBottom:5,color:'#42475C'}}>BOOT STAGE: {startupError.stage}</Text>
      <Text testID='bootstrap-boot-error' selectable style={{fontSize:12,marginBottom:5,color:'#8A2942'}}>BOOT ERROR: {startupError.code} · {startupError.errorName}: {startupError.errorMessage}</Text>
      <Text selectable style={{fontSize:11,marginBottom:4,color:'#6A7088'}}>FUNCTION: {startupError.failingFunction}</Text>
      <Text selectable style={{fontSize:11,marginBottom:8,color:'#6A7088'}}>LOCATION: {startupError.failingLocation}</Text>
      <Text testID='bootstrap-retry-count' style={{fontSize:13,fontWeight:'800',marginBottom:10,color:'#20243A'}}>RETRY COUNT: {retryCount}</Text>
      <Pressable testID='bootstrap-retry-button' accessibilityRole='button' disabled={false} collapsable={false} onPress={retry} style={({pressed})=>({minHeight:58,borderRadius:18,backgroundColor:pressed?'#174fc2':'#246bfd',alignItems:'center',justifyContent:'center',paddingHorizontal:24,marginBottom:10})}><Text style={{color:'#fff',fontSize:18,fontWeight:'800'}}>Повторить запуск</Text></Pressable>
      <Pressable accessibilityRole='button' disabled={false} collapsable={false} onPress={()=>{onBootStage('NAVIGATION_READY');setStartupError(null);s.setScreen('auth')}} style={({pressed})=>({minHeight:52,borderRadius:18,borderWidth:1,borderColor:'#246bfd',backgroundColor:pressed?'#E7EEFF':'#FFF',alignItems:'center',justifyContent:'center',paddingHorizontal:24})}><Text style={{color:'#246bfd',fontSize:16,fontWeight:'800'}}>Открыть экран входа</Text></Pressable>
      <Text selectable style={{fontSize:9,lineHeight:12,marginTop:12,color:'#6A7088'}}>STACK: {startupError.stack||'unavailable'}</Text>
    </ScrollView>;
  }
  if(s.screen==='auth')return <LazyAuthScreen/>;
  if(s.screen==='children')return <ScrollView contentContainerStyle={{padding:24,flexGrow:1}}>{visibleChildren.length?<><H1>Кто сегодня занимается?</H1><Body>Выберите ребёнка или добавьте новый профиль.</Body><Button testID='add-child-button' title='＋ Добавить ребёнка' onPress={()=>s.setScreen('add_child')}/>{visibleChildren.map(c=><Card key={c.id}><H2>{c.name}</H2><Body>{c.age?`${c.age} лет · `:''}изучает {c.learningLanguage||'ru'}</Body><Button title='Выбрать' onPress={()=>{s.setSelectedChild(c);s.setScreen(c.activeCharacterId?'home':'hero')}}/></Card>)}</>:<><H1>Добро пожаловать в DOME</H1><Body>Аккаунт подтверждён. Создайте первый профиль ребёнка, чтобы начать занятия.</Body><Button testID='add-child-onboarding-button' title='＋ Добавить ребёнка' onPress={()=>s.setScreen('add_child')}/><Card><H2>Первый шаг</H2><Body>Укажите имя, возраст и языки обучения. После этого вместе выберите героя.</Body></Card></>}<Button secondary title='Выйти из аккаунта' onPress={s.logout}/></ScrollView>;
  if(s.screen==='add_child')return <LazyAddChildScreen/>;
  if(s.screen==='hero')return <LazyHeroScreen/>;
  if(s.screen==='hero_confirm')return <LazyHeroConfirmScreen/>;

  if(s.screen==='language'){
    const c=s.selectedChild;if(!c)return null;
    const save=async()=>{try{setSavingLang(true);const r=await updateChildLanguages(c.id,target,native);s.updateChild({...c,learningLanguage:r.target_language||target,nativeLanguage:r.native_language||native});Alert.alert('Готово','Языки сохранены. Ведущая говорит на изучаемом языке и при необходимости даёт короткую понятную подсказку.');s.setScreen('home')}catch(e:any){Alert.alert('Не удалось сохранить языки',e.message)}finally{setSavingLang(false)}};
    return <ScrollView contentContainerStyle={{padding:24}}><H1>🌍 Изменить языки</H1><Card><H2>Изучаемый язык</H2>{LANGUAGES.map(([code,label])=><Button key={'t'+code} secondary={target!==code} title={`${target===code?'✓ ':''}${label}`} onPress={()=>setTarget(code)}/>)}</Card><Card><H2>Язык объяснений</H2><Body>На этом языке ребёнок получает пояснение после фразы на изучаемом языке.</Body>{LANGUAGES.map(([code,label])=><Button key={'n'+code} secondary={native!==code} title={`${native===code?'✓ ':''}${label}`} onPress={()=>setNative(code)}/>)}</Card><Button disabled={savingLang} title={savingLang?'Сохраняю…':'Сохранить языки'} onPress={save}/><Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>
  }

  if(s.screen==='home')return <ScrollView contentContainerStyle={{padding:24}}><H1>{s.selectedChild?.name||'DOME'}</H1>{s.selectedChild?.heroUrl?<Image source={{uri:s.selectedChild.heroUrl.startsWith('http')?s.selectedChild.heroUrl:API_BASE+s.selectedChild.heroUrl}} style={{height:150,width:'100%',resizeMode:'contain'}}/>:null}<Card><Body>Изучаемый: {s.selectedChild?.learningLanguage||'ru'} · объяснения: {s.selectedChild?.nativeLanguage||'ru'}</Body>{activeLesson?<Body muted>{activeLesson.resume_step!==null?'Можно продолжить:':'Следующий урок:'} {activeLesson.title}</Body>:null}{lessonsError?<Body>Не удалось обновить каталог: {lessonsError}</Body>:null}</Card><Button disabled={lessonsLoading||!activeLesson} title={lessonsLoading?'Загружаю уроки…':activeLesson?.resume_step!==null?'▶ Продолжить урок':'▶ Начать урок'} onPress={()=>activeLesson&&openLesson(activeLesson.lesson_id)}/><Button title='📚 Мои уроки' onPress={()=>s.setScreen('lessons')}/><Button title='🌍 Изменить языки' secondary onPress={()=>s.setScreen('language')}/><Button title='🎭 Мой герой' secondary onPress={()=>s.setScreen('hero')}/><Button title='🎬 Мои мультфильмы' secondary onPress={async()=>{if(s.selectedChild)try{const r=await listMovies(s.selectedChild.id);setMovies(r.movies||[])}catch{}s.setScreen('movies')}}/><Button title='💳 Тарифы и подписка' secondary onPress={()=>s.setScreen('plans')}/><Button title='📊 Мои успехи' secondary onPress={()=>Alert.alert('Прогресс','Данные берутся с сервера DOME.')}/><Button secondary title='Сменить ребёнка' onPress={()=>s.setScreen('children')}/></ScrollView>;
  if(s.screen==='plans'||s.screen==='purchase')return <LazyPurchaseScreen/>;
  if(s.screen==='lessons')return <ScrollView contentContainerStyle={{padding:24}}><H1>Мои уроки</H1><Button secondary disabled={lessonsLoading} title={lessonsLoading?'Обновляю…':'↻ Обновить каталог'} onPress={()=>setCatalogReloadNonce(value=>value+1)}/>{lessonsLoading?<Card><Body>Обновляю опубликованный каталог…</Body></Card>:null}{lessonsError?<Card><Body>{lessonsError}</Body></Card>:null}{lessons.map(item=><Card key={`${item.course_id}:${item.lesson_id}`}><H2>{item.title}</H2><Body>{item.description||item.course_title}</Body><Body muted>Пройдено: {item.completed_runs}/{item.max_completed_runs}{item.resume_step!==null?' · есть сохранённый прогресс':''}</Body><Button disabled={!item.available} title={item.available?(item.resume_step!==null?'Продолжить':'Начать'):'🔒 Недоступен'} onPress={()=>openLesson(item.lesson_id)}/></Card>)}{!lessonsLoading&&!lessons.length?<Card><Body>Опубликованных уроков пока нет.</Body></Card>:null}<Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>;
  if(s.screen==='lesson')return activeLessonId?<Suspense fallback={<View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Body>Открываем урок…</Body></View>}><LazyLessonPlayer lessonId={activeLessonId}/></Suspense>:<View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Body>Урок не выбран.</Body><Button title='К списку уроков' onPress={()=>s.setScreen('lessons')}/></View>;
  if(s.screen==='movies')return <ScrollView contentContainerStyle={{padding:24}}><H1>Мои мультфильмы</H1>{movies.length?movies.map((m:any)=><Card key={`${m.session_id}-${m.created_at}`}><Body>{m.title||m.filename}</Body><Body muted>{m.status==='READY'?'Готов':m.status==='PROCESSING'?'Обрабатывается…':m.status==='FAILED'?'Нужен повторный запуск':'Ожидает данных'} · {m.created_at||''}</Body>{m.url?<><Button title='▶ Смотреть / скачать' onPress={()=>Linking.openURL(m.url)}/><Button secondary title='Поделиться' onPress={()=>Share.share({message:m.url,url:m.url})}/></>:null}</Card>):<Card><Body>Пока мультфильмов нет.</Body></Card>}<Button secondary title='Обновить' onPress={async()=>{if(s.selectedChild){const r=await listMovies(s.selectedChild.id);setMovies(r.movies||[])}}}/><Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>;
  if(s.screen==='admin')return <LazyAdminScreen/>;
  return <View style={{padding:24}}><Text>Неизвестный экран</Text></View>
}
