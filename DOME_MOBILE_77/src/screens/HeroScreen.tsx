import React,{useState} from 'react';
import {ActivityIndicator,Alert,Image,ScrollView,Text,View} from 'react-native';
import * as ImagePicker from 'expo-image-picker';

import {choosePresetHero,uploadHero} from '../api/mobile';
import {Button,Card,H1,H2,Body} from '../components/Ui';
import {presetHeroes} from '../data/heroes';
import {useAppStore} from '../store/AppStore';

export function HeroScreen(){
  const store=useAppStore();const child=store.selectedChild;const[busy,setBusy]=useState(false);const[processingCustom,setProcessingCustom]=useState(false);
  if(!child)return null;
  const customHero=Boolean(child.heroUrl&&child.heroMetadata&&child.heroMetadata.source!=='preset_catalog');

  const preset=async(id:string)=>{
    try{
      setBusy(true);
      const response=await choosePresetHero(child.id,id);
      store.updateChild({...child,activeCharacterId:response.character_id,heroUrl:response.hero_url,heroMetadata:response.hero_metadata});
      // Catalog rigs are authored, trusted and already confirmed. Anatomy UI is
      // reserved for child drawings and must never delay a preset selection.
      store.setScreen('home');
    }catch(error:any){Alert.alert('Не удалось выбрать героя',error.message)}finally{setBusy(false)}
  };

  const upload=async()=>{
    const permission=await ImagePicker.requestMediaLibraryPermissionsAsync();
    if(!permission.granted){Alert.alert('Нужен доступ к фото');return}
    const result=await ImagePicker.launchImageLibraryAsync({mediaTypes:ImagePicker.MediaTypeOptions.Images,quality:0.9,allowsEditing:false});
    const asset=result.assets?.[0];if(result.canceled||!asset)return;
    try{
      setBusy(true);setProcessingCustom(true);
      const response=await uploadHero(child.id,asset.uri);
      store.updateChild({...child,activeCharacterId:response.character_id,heroUrl:response.hero_url,heroMetadata:response.hero_metadata});
      store.setScreen('hero_confirm');
    }catch(error:any){Alert.alert('Не удалось подготовить героя',error.message)}finally{setProcessingCustom(false);setBusy(false)}
  };

  if(processingCustom)return <View testID='custom-avatar-processing' accessibilityLiveRegion='polite' style={{flex:1,alignItems:'center',justifyContent:'center',padding:28,gap:18,backgroundColor:'#f7fbff'}}><ActivityIndicator size='large' color='#246bfd'/><H2>Загружаем и распознаём твоего героя…</H2><Body>Убираем фон и находим голову, лапы, ноги и точку опоры. Это займёт немного времени.</Body></View>;

  return <ScrollView contentContainerStyle={{padding:24}}><H1>Выбери своего героя</H1><Body>Этот герой будет появляться в уроке и в персональном мультфильме.</Body>
    {customHero?<Card><H2>Текущий рисунок</H2><Body>Подтверждённую разметку головы, направления и точки опоры можно проверить в любое время.</Body><Button secondary title='Проверить разметку героя' onPress={()=>store.setScreen('hero_confirm')}/></Card>:null}
    <View style={{flexDirection:'row',flexWrap:'wrap',gap:10}}>{presetHeroes.map(hero=><View key={hero.id} style={{width:'47%'}}><Card><Image source={hero.image} style={{height:130,width:'100%',resizeMode:'contain'}}/><Text style={{textAlign:'center',fontWeight:'700'}}>{hero.title}</Text><Button disabled={busy} title='Выбрать' onPress={()=>preset(hero.id)}/></Card></View>)}</View>
    <Card><H2>Или загрузи своего героя</H2><Body>Лучше всего — рисунок целиком на светлом фоне. DOME автоматически уберёт фон, распознает тело и затем один раз покажет разметку для подтверждения.</Body><Button disabled={busy} title='📷 Загрузить героя' onPress={upload}/></Card>
    <Button secondary title='Назад' onPress={()=>store.setScreen(child.activeCharacterId?'home':'children')}/>
  </ScrollView>;
}
