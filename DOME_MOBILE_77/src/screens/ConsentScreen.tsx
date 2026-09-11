import React,{useState} from 'react';
import {Alert,ScrollView,View} from 'react-native';
import {Body,Button,Card,CheckRow,H1,H2} from '../components/Ui';
import {useAppStore} from '../store/AppStore';

export function ConsentScreen(){
  const s=useAppStore();
  const[consentPersonalData,setConsentPersonalData]=useState(true);
  const[consentVoice,setConsentVoice]=useState(true);
  const[consentCartoon,setConsentCartoon]=useState(true);
  const[consentMarketing,setConsentMarketing]=useState(false);

  const allRequired=consentPersonalData && consentVoice && consentCartoon;

  const onConfirm=()=>{
    if(!allRequired){
      Alert.alert('Обязательные согласия','Для продолжения необходимо согласие на обработку данных, запись голоса и создание мультфильма.');
      return;
    }
    s.setConsentsAccepted(true);
    s.setScreen('children');
  };

  return (
    <ScrollView contentContainerStyle={{padding:24,paddingBottom:48}}>
      <H1>Согласия и безопасность</H1>
      <Body muted>В соответствии с политикой защиты данных и правилами работы с детьми (COPPA / GDPR-K / 152-ФЗ), для обучения необходимы согласия родителя или законного представителя.</Body>

      <Card>
        <H2>1. Обработка данных ребёнка *</H2>
        <Body>Я подтверждаю, что являюсь родителем (законным представителем) ребёнка, и даю согласие на обработку предоставленных персональных данных (имя, возраст, прогресс в обучении) исключительно в целях персонализации образовательного процесса в DOME.</Body>
        <CheckRow
          checked={consentPersonalData}
          title="Даю согласие на обработку данных ребёнка"
          onPress={()=>setConsentPersonalData(v=>!v)}
        />
      </Card>

      <Card>
        <H2>2. Запись и распознавание голоса *</H2>
        <Body>Я разрешаю приложению DOME производить запись голоса ребёнка во время учебных диалогов для оценки произношения, интерактивного общения с ведущей Милой и формирования индивидуальной образовательной траектории.</Body>
        <CheckRow
          checked={consentVoice}
          title="Даю согласие на запись и распознавание голоса"
          onPress={()=>setConsentVoice(v=>!v)}
        />
      </Card>

      <Card>
        <H2>3. Создание персонализированного мультфильма *</H2>
        <Body>Я разрешаю использовать реплики ребёнка из пройденного урока и выбранного персонажа для генерации персонального обучающего мультфильма, доступного только в данном аккаунте.</Body>
        <CheckRow
          checked={consentCartoon}
          title="Даю согласие на создание мультфильма с репликами ребёнка"
          onPress={()=>setConsentCartoon(v=>!v)}
        />
      </Card>

      <Card>
        <H2>4. Новости и обновления курса (опционально)</H2>
        <Body>Согласие на получение информации о новых интерактивных уроках, обновлениях персонажей и специальных предложениях.</Body>
        <CheckRow
          checked={consentMarketing}
          title="Получать новости об уроках и персонажах"
          onPress={()=>setConsentMarketing(v=>!v)}
        />
      </Card>

      <View style={{marginTop:12}}>
        <Button
          disabled={!allRequired}
          title="Принять и продолжить ›"
          onPress={onConfirm}
        />
      </View>
    </ScrollView>
  );
}

