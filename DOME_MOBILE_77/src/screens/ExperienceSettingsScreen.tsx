import React,{useEffect,useState} from 'react';
import {ScrollView} from 'react-native';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {loadExperiencePreferences,setExperiencePreferences,subscribeExperiencePreferences,type ExperiencePreferences} from '../experience/experience';
import {useAppStore} from '../store/AppStore';

export function ExperienceSettingsScreen(){
  const store=useAppStore();const[prefs,setPrefs]=useState<ExperiencePreferences>({soundEffects:true,haptics:true});
  useEffect(()=>{const unsubscribe=subscribeExperiencePreferences(setPrefs);void loadExperiencePreferences().then(setPrefs);return unsubscribe},[]);
  const update=(value:Partial<ExperiencePreferences>)=>void setExperiencePreferences(value).then(setPrefs);
  return <ScrollView contentContainerStyle={{padding:24}}><H1>Звук и отклик</H1>
    <Card><H2>Звуковые эффекты</H2><Body>Мягкие звуки нажатий, успеха, повтора и сборки мультфильма.</Body><Button secondary={!prefs.soundEffects} title={`${prefs.soundEffects?'✓ ':''}Звуки ${prefs.soundEffects?'включены':'выключены'}`} onPress={()=>update({soundEffects:!prefs.soundEffects})}/></Card>
    <Card><H2>Тактильная отдача</H2><Body>Короткие ненавязчивые отклики на нажатия, drag-and-drop и успешные задания.</Body><Button secondary={!prefs.haptics} title={`${prefs.haptics?'✓ ':''}Отклик ${prefs.haptics?'включён':'выключён'}`} onPress={()=>update({haptics:!prefs.haptics})}/></Card>
    <Body muted>Если устройство не поддерживает эффект, DOME продолжит работу без ошибки.</Body><Button secondary title='Назад' onPress={()=>store.setScreen('home')}/>
  </ScrollView>;
}
