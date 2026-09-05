import React,{useRef,useState} from 'react';
import {Alert,Keyboard,Pressable,Text,TextInput,View} from 'react-native';
import {createChild} from '../api/mobile';
import {KeyboardAwareForm} from '../components/KeyboardAwareForm';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {useAppStore} from '../store/AppStore';
import {theme} from '../theme/theme';
import {ChildProfile} from '../types/domain';
import {EXPLANATION_LANGUAGE_OPTIONS,STUDIED_LANGUAGE_CODE,STUDIED_LANGUAGE_OPTIONS} from '../data/languagePolicy';

function LanguagePicker({options,value,onChange,compact}:{options:readonly (readonly [string,string])[];value:string;onChange:(value:string)=>void;compact:boolean}){
  return <View style={{flexDirection:'row',flexWrap:'wrap',gap:compact?5:8,marginBottom:compact?5:10}}>
    {options.map(([code,label])=>{
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
  const targetLanguage=STUDIED_LANGUAGE_CODE;
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
        languageLevel:response.language_level||'PRE_A1',
        workingDifficulty:Number(response.working_difficulty??0.15),
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
      {!compact?<H1>Добавить ребёнка</H1>:null}
      <Card compact={compact}>
        <H2 compact={compact}>{compact?'Добавить ребёнка':'Знакомство с DOME'}</H2>
        {!compact?<Body>Укажите основные данные — их можно будет изменить позже.</Body>:null}
        <TextInput value={name} onChangeText={setName} placeholder='Имя ребёнка' autoCapitalize='words' returnKeyType='next' onFocus={onFieldFocus} onSubmitEditing={()=>ageRef.current?.focus()} style={[input,compact&&compactInput]}/>
        <TextInput ref={ageRef} value={age} onChangeText={value=>setAge(value.replace(/\D/g,'').slice(0,2))} placeholder='Возраст (2–18)' keyboardType='number-pad' returnKeyType='done' onFocus={onFieldFocus} onSubmitEditing={Keyboard.dismiss} style={[input,compact&&compactInput]}/>
        {compact?<Body compact>Изучаемый: Русский · объяснения: {nativeLanguage.toUpperCase()}. Закройте клавиатуру, чтобы изменить.</Body>:<>
          <H2>Изучаемый язык</H2>
          <LanguagePicker options={STUDIED_LANGUAGE_OPTIONS} compact={false} value={targetLanguage} onChange={()=>{}}/>
          <H2>Язык объяснений</H2>
          <LanguagePicker options={EXPLANATION_LANGUAGE_OPTIONS} compact={false} value={nativeLanguage} onChange={setNativeLanguage}/>
        </>}
        {inlineAction}
        {!compact?<Button secondary disabled={busy} title='Назад' onPress={()=>{Keyboard.dismiss();store.setScreen('children')}}/>:null}
      </Card>
    </>}
  </KeyboardAwareForm>
}

const input={borderWidth:1,borderColor:'#CCC',borderRadius:14,padding:14,fontSize:16,marginBottom:12,backgroundColor:'#FFF'} as const;
const compactInput={borderRadius:9,paddingVertical:6,paddingHorizontal:10,fontSize:14,lineHeight:17,marginBottom:3} as const;
