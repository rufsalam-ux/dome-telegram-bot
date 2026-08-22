import React,{useRef,useState} from 'react';
import {Alert,Keyboard,Pressable,Text,TextInput,View} from 'react-native';
import {createChild} from '../api/mobile';
import {KeyboardAwareForm} from '../components/KeyboardAwareForm';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {useAppStore} from '../store/AppStore';
import {theme} from '../theme/theme';
import {ChildProfile} from '../types/domain';

const LANGUAGES=[
  ['ru','Русский'],['en','English'],['es','Español'],['de','Deutsch'],['fr','Français'],
  ['it','Italiano'],['pt','Português'],['tr','Türkçe'],['ar','العربية'],['zh','中文'],
] as const;

function LanguagePicker({value,onChange,compact}:{value:string;onChange:(value:string)=>void;compact:boolean}){
  return <View style={{flexDirection:'row',flexWrap:'wrap',gap:compact?5:8,marginBottom:compact?5:10}}>
    {LANGUAGES.map(([code,label])=>{
      const selected=value===code;
      return <Pressable key={code} accessibilityRole='button' accessibilityState={{selected}} onPress={()=>onChange(code)} style={{borderWidth:1,borderColor:theme.colors.primary,backgroundColor:selected?theme.colors.primary:'#FFF',borderRadius:999,paddingHorizontal:compact?9:12,paddingVertical:compact?5:8}}>
        <Text style={{color:selected?'#FFF':theme.colors.primary,fontWeight:'700',fontSize:compact?12:14}}>{label}</Text>
      </Pressable>;
    })}
  </View>
}

export function AddChildScreen(){
  const store=useAppStore();
  const ageRef=useRef<TextInput>(null);
  const[name,setName]=useState('');
  const[age,setAge]=useState('');
  const[targetLanguage,setTargetLanguage]=useState('en');
  const[nativeLanguage,setNativeLanguage]=useState('ru');
  const[busy,setBusy]=useState(false);
  const ageNumber=Number(age);
  const validAge=/^\d{1,2}$/.test(age)&&ageNumber>=2&&ageNumber<=18;

  const save=async()=>{
    Keyboard.dismiss();
    if(!name.trim()||!validAge)return;
    try{
      setBusy(true);
      const response=await createChild(name.trim(),ageNumber,targetLanguage,nativeLanguage);
      const child:ChildProfile={
        id:String(response.id),
        parentId:String(store.parent?.id||''),
        name:response.name||response.display_name||name.trim(),
        age:response.age_years??ageNumber,
        learningLanguage:response.target_language||targetLanguage,
        nativeLanguage:response.native_language||nativeLanguage,
        courseId:'conversation',
        activeCharacterId:response.active_character_id??null,
        heroUrl:response.hero_url??null,
      };
      store.addChild(child);
      store.setSelectedChild(child);
      store.setScreen('hero');
      Alert.alert('Профиль создан','Теперь выберите героя ребёнка.');
    }catch(error:any){
      Alert.alert('Не удалось добавить ребёнка',error?.message||'Попробуйте ещё раз');
    }finally{setBusy(false)}
  };

  const action=(compact:boolean)=><Button compact={compact} disabled={busy||!name.trim()||!validAge} title={busy?'Добавляю…':'Добавить ребёнка'} onPress={save}/>;
  return <KeyboardAwareForm primaryAction={action}>
    {({compact,onFieldFocus},inlineAction)=><>
      <H1 compact={compact}>Добавить ребёнка</H1>
      <Card compact={compact}>
        <H2 compact={compact}>Знакомство с DOME</H2>
        <Body compact={compact}>Укажите основные данные — их можно будет изменить позже.</Body>
        <TextInput value={name} onChangeText={setName} placeholder='Имя ребёнка' autoCapitalize='words' returnKeyType='next' onFocus={onFieldFocus} onSubmitEditing={()=>ageRef.current?.focus()} style={[input,compact&&compactInput]}/>
        <TextInput ref={ageRef} value={age} onChangeText={value=>setAge(value.replace(/\D/g,'').slice(0,2))} placeholder='Возраст (2–18)' keyboardType='number-pad' returnKeyType='done' onFocus={onFieldFocus} onSubmitEditing={Keyboard.dismiss} style={[input,compact&&compactInput]}/>
        <H2 compact={compact}>Язык занятий</H2>
        <LanguagePicker compact={compact} value={targetLanguage} onChange={setTargetLanguage}/>
        <H2 compact={compact}>Язык объяснений</H2>
        <LanguagePicker compact={compact} value={nativeLanguage} onChange={setNativeLanguage}/>
        {inlineAction}
        <Button compact={compact} secondary disabled={busy} title='Назад' onPress={()=>{Keyboard.dismiss();store.setScreen('children')}}/>
      </Card>
    </>}
  </KeyboardAwareForm>
}

const input={borderWidth:1,borderColor:'#CCC',borderRadius:14,padding:14,fontSize:16,marginBottom:12,backgroundColor:'#FFF'} as const;
const compactInput={borderRadius:11,paddingVertical:9,paddingHorizontal:12,fontSize:15,marginBottom:6} as const;
