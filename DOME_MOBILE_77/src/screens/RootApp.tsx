import React,{useEffect,useRef,useState} from 'react';
import {Alert,Image,Pressable,ScrollView,Share,Text,useWindowDimensions,View} from 'react-native';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {MoviePlayer} from '../components/MoviePlayer';
import {useAppStore} from '../store/AppStore';
import {bootstrap,isUnauthorizedError,listLessons,listMovies,API_BASE,restoreApiToken,retryMovieBuild,updateChildLanguages} from '../api/mobile';
import {BootStage,logStartupStage,startupFailure,StartupFailure,withStartupTimeout} from '../engine/startup';
import {AuthScreen} from './AuthScreen';
import {playExperience} from '../experience/experience';
import {movieIdentity,normalizeMovieState,MOVIE_ACTIVE_STATES,MOVIE_RETRY_STATES,MOVIE_SUCCESS_STATES} from '../engine/movieRuntime';
import {homeMenuLayoutPolicy} from '../engine/homeRuntime';

// Optional native screens use synchronous, statically analyzable Metro requires.
// Their code is part of the main bundle, but native media modules are evaluated
// only when the corresponding route opens. No network-loaded JS chunk is used.

const LANGUAGES=[
  ['ru','Русский'],['en','English'],['es','Español'],['de','Deutsch'],['fr','Français'],
  ['it','Italiano'],['pt','Português'],['tr','Türkçe'],['ar','العربية'],['zh','中文']
] as const;

function normalizedListedMovie(raw:any):any{
  const movie=normalizeMovieState(raw,Number(raw?.session_id||0));
  return {...raw,...movie,url:movie.movie_url,status:movie.status,job_id:movie.job_id,attempt_id:movie.attempt_id,stage:movie.stage,progress:movie.progress,error_code:movie.error_code,error_message:movie.error_message};
}

type RootAppProps={
  onBootStage:(stage:BootStage,failure?:StartupFailure|null)=>void;
  retryCount:number;
  onRetryReceived:()=>number;
};

type HomeMenuItem={title:string;onPress:()=>void;disabled?:boolean};

function HomeMenu({store,activeLesson,lessonsLoading,lessonsError,openLesson}:{store:ReturnType<typeof useAppStore>;activeLesson:any;lessonsLoading:boolean;lessonsError:string;openLesson:(lessonId:string)=>void}){
  const dimensions=useWindowDimensions();const insets=useSafeAreaInsets();const layout=homeMenuLayoutPolicy(dimensions.width,dimensions.height,insets.bottom);const child=store.selectedChild;
  const items:HomeMenuItem[]=[
    {title:'📚 Мои уроки',onPress:()=>store.setScreen('lessons')},
    {title:'🌍 Языки',onPress:()=>store.setScreen('language')},
    {title:'🎭 Мой герой',onPress:()=>store.setScreen('hero')},
    {title:'🎬 Мультфильмы',onPress:()=>store.setScreen('movies')},
    {title:'💳 Тарифы',onPress:()=>store.setScreen('plans')},
    {title:'🔊 Звук',onPress:()=>store.setScreen('experience_settings')},
    {title:'📊 Успехи',onPress:()=>Alert.alert('Прогресс','Данные берутся с сервера DOME.')},
    {title:'👨‍👩‍👧 Сменить ребёнка',onPress:()=>store.setScreen('children')},
  ];
  const rows=Array.from({length:Math.ceil(items.length/layout.columns)},(_,row)=>items.slice(row*layout.columns,(row+1)*layout.columns));
  const heroUri=child?.heroUrl?(child.heroUrl.startsWith('http')?child.heroUrl:API_BASE+child.heroUrl):'';
  return <View testID='home-menu-screen' style={{flex:1,padding:layout.contentPadding,paddingBottom:layout.contentPadding+insets.bottom,gap:layout.gap}}>
    <View style={{height:layout.heroSize,flexDirection:'row',alignItems:'center',justifyContent:'space-between',gap:10}}>
      <View style={{flex:1}}><H1 compact={layout.compact}>{child?.name||'DOME'}</H1><Body compact={layout.compact} muted>Изучаемый: {child?.learningLanguage||'ru'} · объяснения: {child?.nativeLanguage||'ru'}</Body></View>
      {heroUri?<Image source={{uri:heroUri}} style={{height:layout.heroSize,width:layout.heroSize,resizeMode:'contain'}}/>:null}
    </View>
    <Card compact><Body compact={layout.compact}>{activeLesson?`${activeLesson.resume_step!==null?'Можно продолжить':'Следующий урок'}: ${activeLesson.title}`:lessonsError||'Обновляем каталог уроков…'}</Body></Card>
    <Button compact disabled={lessonsLoading||!activeLesson} title={lessonsLoading?'Загружаю уроки…':activeLesson?.resume_step!==null?'▶ Продолжить урок':'▶ Начать урок'} onPress={()=>activeLesson&&openLesson(activeLesson.lesson_id)}/>
    <View testID='home-menu-grid' style={{flex:1,justifyContent:'center',gap:layout.gap}}>{rows.map((row,rowIndex)=><View key={rowIndex} style={{flexDirection:'row',gap:layout.gap}}>{row.map(item=><View key={item.title} style={{flex:1,minHeight:layout.tileHeight}}><Button compact secondary title={item.title} disabled={item.disabled} onPress={item.onPress}/></View>)}{Array.from({length:layout.columns-row.length},(_,index)=><View key={`empty-${index}`} style={{flex:1}}/>)}</View>)}</View>
  </View>;
}

export function RootApp({onBootStage,retryCount,onRetryReceived}:RootAppProps){
  const s=useAppStore();
  const[movies,setMovies]=useState<any[]>([]);
  const[openedMovie,setOpenedMovie]=useState<any|null>(null);
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
  const[movieReloadNonce,setMovieReloadNonce]=useState(0);
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
  useEffect(()=>{if(s.screen!=='movies'||!s.selectedChild)return;let active=true;let timer:any;const poll=async()=>{console.info('MOVIE_MOBILE_POLL_START',{child_id:s.selectedChild!.id,source:'MOVIE_LIBRARY'});try{const result=await listMovies(s.selectedChild!.id);if(!active)return;const items=(result.movies||[]).map(normalizedListedMovie);console.info('MOVIE_MOBILE_POLL_RESPONSE',{child_id:s.selectedChild!.id,count:items.length,statuses:items.map((movie:any)=>({session_id:movie.session_id,run_id:movie.run_id,status:movie.status,job_id:movie.job_id,attempt_id:movie.attempt_id,movie_url:movie.movie_url}))});for(const movie of items.filter((item:any)=>MOVIE_SUCCESS_STATES.has(item.status)&&item.movie_url)){console.info('MOVIE_MOBILE_READY_RECEIVED',movieIdentity(movie));console.info('MOVIE_MOBILE_URL_SET',movieIdentity(movie))}setMovies(items);setOpenedMovie((current:any)=>{if(current){const updated=items.find((item:any)=>item.session_id===current.session_id);if(updated?.movie_url)return updated}return items.find((item:any)=>MOVIE_SUCCESS_STATES.has(item.status)&&item.movie_url)||null});if(items.some((item:any)=>MOVIE_ACTIVE_STATES.has(item.status)))timer=setTimeout(poll,2500)}catch(error:any){console.warn('MOVIE_MOBILE_POLL_RESPONSE',{child_id:s.selectedChild!.id,status:'NETWORK_ERROR',error:String(error?.message||error)});if(active)timer=setTimeout(poll,4000)}};void poll();return()=>{active=false;if(timer)clearTimeout(timer)}},[s.screen,s.selectedChild?.id,movieReloadNonce]);
  useEffect(()=>{let active=true;const child=s.selectedChild;if(!child){setLessons([]);setActiveLessonId('');return}setLessonsLoading(true);setLessonsError('');void listLessons(child.id).then(data=>{if(!active)return;const items=Array.isArray(data?.lessons)?data.lessons:[];setLessons(items);const first=items.find((item:any)=>item.available&&item.resume_step!==null)||items.find((item:any)=>item.available);setActiveLessonId(current=>items.some((item:any)=>item.lesson_id===current&&item.available)?current:String(first?.lesson_id||''))}).catch(error=>{if(active&&!isUnauthorizedError(error)){setLessons([]);setActiveLessonId('');setLessonsError(error.message||'Не удалось загрузить уроки')}}).finally(()=>{if(active)setLessonsLoading(false)});return()=>{active=false}},[s.selectedChild?.id,catalogReloadNonce]);
  const openLesson=(lessonId:string)=>{setActiveLessonId(lessonId);s.setScreen('lesson')};
  const retryListedMovie=async(movie:any)=>{
    if(!MOVIE_RETRY_STATES.has(String(movie.status||'')))return;
    console.info('MOVIE_RETRY_STARTED',movieIdentity(normalizeMovieState(movie,Number(movie.session_id))));
    playExperience('MOVIE_START');
    setMovies(items=>items.map(item=>item.session_id===movie.session_id?{...item,status:'QUEUED',stage:'VALIDATING_RECORDINGS',progress:2,can_retry:false,error_code:null}:item));
    try{
      const result=normalizeMovieState(await retryMovieBuild(Number(movie.session_id)),Number(movie.session_id));
      setMovies(items=>items.map(item=>item.session_id===movie.session_id?normalizedListedMovie({...item,...result}):item));
    }catch{
      setMovies(items=>items.map(item=>item.session_id===movie.session_id?{...item,status:'FAILED',can_retry:true}:item));
      Alert.alert('Не удалось повторить сборку','Все записи сохранены. Проверьте интернет и попробуйте ещё раз.');
    }
  };
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
  if(s.screen==='auth')return <AuthScreen/>;
  if(s.screen==='children')return <ScrollView contentContainerStyle={{padding:24,flexGrow:1}}>{visibleChildren.length?<><H1>Кто сегодня занимается?</H1><Body>Выберите ребёнка или добавьте новый профиль.</Body><Button testID='add-child-button' title='＋ Добавить ребёнка' onPress={()=>s.setScreen('add_child')}/>{visibleChildren.map(c=><Card key={c.id}><H2>{c.name}</H2><Body>{c.age?`${c.age} лет · `:''}изучает {c.learningLanguage||'ru'}</Body><Button title='Выбрать' onPress={()=>{s.setSelectedChild(c);s.setScreen(c.activeCharacterId?'home':'hero')}}/></Card>)}</>:<><H1>Добро пожаловать в DOME</H1><Body>Аккаунт подтверждён. Создайте первый профиль ребёнка, чтобы начать занятия.</Body><Button testID='add-child-onboarding-button' title='＋ Добавить ребёнка' onPress={()=>s.setScreen('add_child')}/><Card><H2>Первый шаг</H2><Body>Укажите имя, возраст и языки обучения. После этого вместе выберите героя.</Body></Card></>}<Button secondary title='Выйти из аккаунта' onPress={s.logout}/></ScrollView>;
  if(s.screen==='add_child'){const {AddChildScreen}=require('./AddChildScreen');return <AddChildScreen/>}
  if(s.screen==='hero'){const {HeroScreen}=require('./HeroScreen');return <HeroScreen/>}
  if(s.screen==='hero_confirm'){const {HeroConfirmScreen}=require('./HeroConfirmScreen');return <HeroConfirmScreen/>}

  if(s.screen==='language'){
    const c=s.selectedChild;if(!c)return null;
    const save=async()=>{try{setSavingLang(true);const r=await updateChildLanguages(c.id,target,native);s.updateChild({...c,learningLanguage:r.target_language||target,nativeLanguage:r.native_language||native});Alert.alert('Готово','Языки сохранены. Ведущая говорит на изучаемом языке и при необходимости даёт короткую понятную подсказку.');s.setScreen('home')}catch(e:any){Alert.alert('Не удалось сохранить языки',e.message)}finally{setSavingLang(false)}};
    return <ScrollView contentContainerStyle={{padding:24}}><H1>🌍 Изменить языки</H1><Card><H2>Изучаемый язык</H2>{LANGUAGES.map(([code,label])=><Button key={'t'+code} secondary={target!==code} title={`${target===code?'✓ ':''}${label}`} onPress={()=>setTarget(code)}/>)}</Card><Card><H2>Язык объяснений</H2><Body>На этом языке ребёнок получает пояснение после фразы на изучаемом языке.</Body>{LANGUAGES.map(([code,label])=><Button key={'n'+code} secondary={native!==code} title={`${native===code?'✓ ':''}${label}`} onPress={()=>setNative(code)}/>)}</Card><Button disabled={savingLang} title={savingLang?'Сохраняю…':'Сохранить языки'} onPress={save}/><Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>
  }

  if(s.screen==='home')return <HomeMenu store={s} activeLesson={activeLesson} lessonsLoading={lessonsLoading} lessonsError={lessonsError} openLesson={openLesson}/>;
  if(s.screen==='experience_settings'){const {ExperienceSettingsScreen}=require('./ExperienceSettingsScreen');return <ExperienceSettingsScreen/>}
  if(s.screen==='plans'||s.screen==='purchase'){const {PurchaseScreen}=require('./PurchaseScreen');return <PurchaseScreen/>}
  if(s.screen==='lessons')return <ScrollView contentContainerStyle={{padding:24}}><H1>Мои уроки</H1><Button secondary disabled={lessonsLoading} title={lessonsLoading?'Обновляю…':'↻ Обновить каталог'} onPress={()=>setCatalogReloadNonce(value=>value+1)}/>{lessonsLoading?<Card><Body>Обновляю опубликованный каталог…</Body></Card>:null}{lessonsError?<Card><Body>{lessonsError}</Body></Card>:null}{lessons.map(item=><Card key={`${item.course_id}:${item.lesson_id}`}><H2>{item.title}</H2><Body>{item.description||item.course_title}</Body><Body muted>Пройдено: {item.completed_runs}/{item.max_completed_runs}{item.resume_step!==null?' · есть сохранённый прогресс':''}</Body><Button disabled={!item.available} title={item.available?(item.resume_step!==null?'Продолжить':'Начать'):'🔒 Недоступен'} onPress={()=>openLesson(item.lesson_id)}/></Card>)}{!lessonsLoading&&!lessons.length?<Card><Body>Опубликованных уроков пока нет.</Body></Card>:null}<Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>;
  if(s.screen==='lesson'){if(activeLessonId){const {LessonPlayer}=require('./LessonPlayer');return <LessonPlayer lessonId={activeLessonId}/>}return <View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Body>Урок не выбран.</Body><Button title='К списку уроков' onPress={()=>s.setScreen('lessons')}/></View>}
  if(s.screen==='movies')return <ScrollView contentContainerStyle={{padding:24}}><H1>Мои мультфильмы</H1>{openedMovie?.movie_url?<Card><H2>{openedMovie.title||openedMovie.filename||'Готовый мультфильм'}</H2><MoviePlayer url={openedMovie.movie_url} identity={movieIdentity(openedMovie)}/><Button secondary title='Поделиться' onPress={()=>Share.share({message:openedMovie.movie_url,url:openedMovie.movie_url})}/></Card>:null}{movies.length?movies.map((m:any)=>{const active=MOVIE_ACTIVE_STATES.has(String(m.status));const ready=MOVIE_SUCCESS_STATES.has(String(m.status));const failed=MOVIE_RETRY_STATES.has(String(m.status));return <Card key={`${m.session_id}-${m.created_at}`}><Body>{m.title||m.filename}</Body><Body muted>{ready?'Готов':active?`${m.stage||'Сборка'} · ${Number(m.progress||0)}%`:failed?'Нужен повторный запуск':'Ожидает данных'} · {m.created_at||''}</Body>{m.error_code?<Body muted>Код: {m.error_code}</Body>:null}{failed?<Button title='Повторить сборку' onPress={()=>void retryListedMovie(m)}/>:null}{m.movie_url?<><Button title='▶ Смотреть' onPress={()=>setOpenedMovie(m)}/><Button secondary title='Поделиться' onPress={()=>Share.share({message:m.movie_url,url:m.movie_url})}/></>:null}</Card>}):<Card><Body>Пока мультфильмов нет.</Body></Card>}<Button secondary title='Обновить' onPress={()=>setMovieReloadNonce(value=>value+1)}/><Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>;
  if(s.screen==='admin'){const {AdminScreen}=require('./AdminScreen');return <AdminScreen/>}
  return <View style={{padding:24}}><Text>Неизвестный экран</Text></View>
}
