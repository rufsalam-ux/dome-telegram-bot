import React from 'react';
import {ScrollView,View} from 'react-native';
import {Body,Button,Card,H1,H2} from '../components/Ui';

// Native app identity is deliberately kept in one small module rather than a
// debug overlay.  EAS preview builds may set the commit marker at build time.
const APP_VERSION='3.1.1';
const ANDROID_VERSION_CODE='30101';
const BUILD_MARKER=String(process.env.EXPO_PUBLIC_BUILD_COMMIT||'local').slice(0,12);

export function AboutScreen({onBack}:{onBack:()=>void}){
  return <ScrollView contentContainerStyle={{padding:24,paddingBottom:42}}>
    <H1>О приложении</H1>
    <Card><H2>DOME</H2><Body>Версия {APP_VERSION}</Body><Body>Android build {ANDROID_VERSION_CODE}</Body><Body muted>Сборка: {BUILD_MARKER}</Body></Card>
    <Card><H2>Поддержка</H2><Body>При обращении в поддержку приложите этот экран — он поможет определить версию приложения.</Body></Card>
    <View style={{marginTop:8}}><Button secondary title='Назад в меню' onPress={onBack}/></View>
  </ScrollView>;
}
