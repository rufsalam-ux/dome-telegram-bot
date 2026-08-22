import React,{useEffect,useState} from 'react';
import {Image,ScrollView,Text,View,Alert} from 'react-native';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {useAppStore} from '../store/AppStore';
import {bootstrap,isUnauthorizedError,listMovies,API_BASE,restoreApiToken,updateChildLanguages} from '../api/mobile';
import {PurchaseScreen} from './PurchaseScreen';
import {LessonPlayer} from './LessonPlayer';
import {AdminScreen} from './AdminScreen';
import {AuthScreen} from './AuthScreen';
import {HeroScreen} from './HeroScreen';
import {AddChildScreen} from './AddChildScreen';

const LANGUAGES=[
  ['ru','Русский'],['en','English'],['es','Español'],['de','Deutsch'],['fr','Français'],
  ['it','Italiano'],['pt','Português'],['tr','Türkçe'],['ar','العربية'],['zh','中文']
] as const;

export function RootApp(){
  const s=useAppStore();
  const[movies,setMovies]=useState<any[]>([]);
  const[target,setTarget]=useState('ru');
  const[native,setNative]=useState('ru');
  const[savingLang,setSavingLang]=useState(false);
  const[sessionReady,setSessionReady]=useState(false);
  const visibleChildren=s.children.filter(child=>Boolean(child?.id&&child?.name?.trim()));

  useEffect(()=>{
    let active=true;
    (async()=>{
      try{
        const token=await restoreApiToken();
        if(!token)return;
        const data=await bootstrap();
        if(active)await s.hydrate(data,token);
      }catch(error){
        if(active&&isUnauthorizedError(error))await s.logout();
      }finally{
        if(active)setSessionReady(true);
      }
    })();
    return()=>{active=false};
  },[]);
  useEffect(()=>{if(s.selectedChild){setTarget(s.selectedChild.learningLanguage||'ru');setNative(s.selectedChild.nativeLanguage||'ru')}},[s.selectedChild?.id]);

  if(!sessionReady)return <View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Body>Восстанавливаем вход…</Body></View>;
  if(s.screen==='auth')return <AuthScreen/>;
  if(s.screen==='children')return <ScrollView contentContainerStyle={{padding:24,flexGrow:1}}>{visibleChildren.length?<><H1>Кто сегодня занимается?</H1><Body>Выберите ребёнка или добавьте новый профиль.</Body><Button testID='add-child-button' title='＋ Добавить ребёнка' onPress={()=>s.setScreen('add_child')}/>{visibleChildren.map(c=><Card key={c.id}><H2>{c.name}</H2><Body>{c.age?`${c.age} лет · `:''}изучает {c.learningLanguage||'ru'}</Body><Button title='Выбрать' onPress={()=>{s.setSelectedChild(c);s.setScreen(c.activeCharacterId?'home':'hero')}}/></Card>)}</>:<><H1>Добро пожаловать в DOME</H1><Body>Аккаунт подтверждён. Создайте первый профиль ребёнка, чтобы начать занятия.</Body><Button testID='add-child-onboarding-button' title='＋ Добавить ребёнка' onPress={()=>s.setScreen('add_child')}/><Card><H2>Первый шаг</H2><Body>Укажите имя, возраст и языки обучения. После этого вместе выберите героя.</Body></Card></>}<Button secondary title='Выйти из аккаунта' onPress={s.logout}/></ScrollView>;
  if(s.screen==='add_child')return <AddChildScreen/>;
  if(s.screen==='hero')return <HeroScreen/>;

  if(s.screen==='language'){
    const c=s.selectedChild;if(!c)return null;
    const save=async()=>{try{setSavingLang(true);const r=await updateChildLanguages(c.id,target,native);s.updateChild({...c,learningLanguage:r.target_language||target,nativeLanguage:r.native_language||native});Alert.alert('Готово','Языки сохранены. Следующий урок будет озвучиваться сначала на изучаемом языке, затем на понятном ребёнку.');s.setScreen('home')}catch(e:any){Alert.alert('Не удалось сохранить языки',e.message)}finally{setSavingLang(false)}};
    return <ScrollView contentContainerStyle={{padding:24}}><H1>🌍 Изменить языки</H1><Card><H2>Изучаемый язык</H2>{LANGUAGES.map(([code,label])=><Button key={'t'+code} secondary={target!==code} title={`${target===code?'✓ ':''}${label}`} onPress={()=>setTarget(code)}/>)}</Card><Card><H2>Язык объяснений</H2><Body>На этом языке ребёнок получает пояснение после фразы на изучаемом языке.</Body>{LANGUAGES.map(([code,label])=><Button key={'n'+code} secondary={native!==code} title={`${native===code?'✓ ':''}${label}`} onPress={()=>setNative(code)}/>)}</Card><Button disabled={savingLang} title={savingLang?'Сохраняю…':'Сохранить языки'} onPress={save}/><Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>
  }

  if(s.screen==='home')return <ScrollView contentContainerStyle={{padding:24}}><H1>{s.selectedChild?.name||'DOME'}</H1>{s.selectedChild?.heroUrl?<Image source={{uri:s.selectedChild.heroUrl.startsWith('http')?s.selectedChild.heroUrl:API_BASE+s.selectedChild.heroUrl}} style={{height:150,width:'100%',resizeMode:'contain'}}/>:null}<Card><Body>Изучаемый: {s.selectedChild?.learningLanguage||'ru'} · объяснения: {s.selectedChild?.nativeLanguage||'ru'}</Body></Card><Button title='▶ Продолжить урок' onPress={()=>s.setScreen('lesson')}/><Button title='📚 Мои уроки' onPress={()=>s.setScreen('lessons')}/><Button title='🌍 Изменить языки' secondary onPress={()=>s.setScreen('language')}/><Button title='🎭 Мой герой' secondary onPress={()=>s.setScreen('hero')}/><Button title='🎬 Мои мультфильмы' secondary onPress={async()=>{if(s.selectedChild)try{const r=await listMovies(s.selectedChild.id);setMovies(r.movies||[])}catch{}s.setScreen('movies')}}/><Button title='💳 Тарифы и подписка' secondary onPress={()=>s.setScreen('plans')}/><Button title='📊 Мои успехи' secondary onPress={()=>Alert.alert('Прогресс','Данные берутся с сервера DOME.')}/><Button secondary title='Сменить ребёнка' onPress={()=>s.setScreen('children')}/></ScrollView>;
  if(s.screen==='plans'||s.screen==='purchase')return <PurchaseScreen/>;
  if(s.screen==='lessons')return <ScrollView contentContainerStyle={{padding:24}}><H1>Разговорные занятия</H1><Card><H2>Путешествие: Мадагаскар и Исландия</H2><Body>Полный урок DOME с живой AI-озвучкой, голосовыми ответами, героем и мультфильмом.</Body><Button title='Начать' onPress={()=>s.setScreen('lesson')}/></Card><Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>;
  if(s.screen==='lesson')return <LessonPlayer/>;
  if(s.screen==='movies')return <ScrollView contentContainerStyle={{padding:24}}><H1>Мои мультфильмы</H1>{movies.length?movies.map((m:any)=><Card key={m.url}><Body>{m.title||m.filename}</Body><Body muted>{m.created_at||''}</Body></Card>):<Card><Body>Пока мультфильмов нет.</Body></Card>}<Button secondary title='Назад' onPress={()=>s.setScreen('home')}/></ScrollView>;
  if(s.screen==='admin')return <AdminScreen/>;
  return <View style={{padding:24}}><Text>Неизвестный экран</Text></View>
}
