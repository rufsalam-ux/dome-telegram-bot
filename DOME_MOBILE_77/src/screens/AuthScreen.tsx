import React,{useRef,useState} from 'react';
import {Alert,Keyboard,TextInput} from 'react-native';
import * as SecureStore from 'expo-secure-store';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {KeyboardAwareForm} from '../components/KeyboardAwareForm';
import {confirmPasswordReset,loginAccount,registerAccount,resendVerification,requestPasswordReset,setApiToken,verifyEmail} from '../api/mobile';
import {useAppStore} from '../store/AppStore';

type Mode='login'|'register'|'verify'|'forgot'|'reset';

export function AuthScreen(){
  const store=useAppStore();
  const nameRef=useRef<TextInput>(null);
  const emailRef=useRef<TextInput>(null);
  const passwordRef=useRef<TextInput>(null);
  const codeRef=useRef<TextInput>(null);
  const[mode,setMode]=useState<Mode>('login');
  const[name,setName]=useState('');
  const[email,setEmail]=useState('');
  const[password,setPassword]=useState('');
  const[code,setCode]=useState('');
  const[busy,setBusy]=useState(false);
  const cleanEmail=email.trim().toLowerCase();

  const finish=async(result:any)=>{
    if(!result?.token)throw new Error('Сервер не вернул сессию');
    setApiToken(result.token);
    await SecureStore.setItemAsync('dome_mobile_token',result.token);
    await store.hydrate(result,result.token);
  };

  const submit=async()=>{
    Keyboard.dismiss();
    try{
      setBusy(true);
      if(mode==='login'){
        try{await finish(await loginAccount(cleanEmail,password))}
        catch(error:any){
          if(error?.code==='EMAIL_NOT_VERIFIED'){
            setMode('verify');setCode('');
            Alert.alert('Подтвердите email','Введите шестизначный код из письма.');
          }else throw error;
        }
      }else if(mode==='register'){
        const result=await registerAccount(name.trim(),cleanEmail,password);
        if(result.verification_required){
          setMode('verify');setCode('');
          Alert.alert('Введите код из письма','Мы отправили шестизначный код подтверждения. Он действует 10 минут.');
        }
      }else if(mode==='verify'){
        await finish(await verifyEmail(cleanEmail,code));
      }else if(mode==='forgot'){
        await requestPasswordReset(cleanEmail);setMode('reset');setCode('');
        Alert.alert('Проверьте почту','Если аккаунт существует, код восстановления отправлен.');
      }else{
        await confirmPasswordReset(cleanEmail,code,password);setMode('login');setCode('');
        Alert.alert('Готово','Пароль изменён. Теперь войдите.');
      }
    }catch(error:any){
      Alert.alert('DOME',error?.message||'Не удалось выполнить действие');
    }finally{setBusy(false)}
  };

  const resend=async()=>{
    Keyboard.dismiss();
    try{
      setBusy(true);
      const result=await resendVerification(cleanEmail);
      if(result.already_verified){setMode('login');setCode('');Alert.alert('Email уже подтверждён','Войдите с email и паролем.');return}
      setCode('');Alert.alert('Код отправлен','Новый шестизначный код отправлен на почту и действует 10 минут.');
    }catch(error:any){Alert.alert('DOME',error?.message||'Не удалось отправить код')}
    finally{setBusy(false)}
  };

  const title=mode==='login'?'Войти':mode==='register'?'Создать аккаунт':mode==='verify'?'Введите код из письма':mode==='forgot'?'Восстановить пароль':'Новый пароль';
  const blocked=busy||!cleanEmail||(mode==='register'&&!name.trim())||((mode==='login'||mode==='register'||mode==='reset')&&password.length<8)||((mode==='verify'||mode==='reset')&&code.length!==6);
  const action=(compact:boolean)=><Button compact={compact} disabled={blocked} title={busy?'Подождите…':mode==='login'?'Войти':mode==='register'?'Создать аккаунт':mode==='verify'?'Подтвердить email':mode==='forgot'?'Отправить код':'Сохранить новый пароль'} onPress={submit}/>;
  return <KeyboardAwareForm primaryAction={action}>
    {({compact,onFieldFocus},inlineAction)=><>
      {!compact?<H1>DOME</H1>:null}
      <Card compact={compact}><H2 compact={compact}>{title}</H2>
        {mode==='register'?<TextInput ref={nameRef} value={name} onChangeText={setName} placeholder='Имя' autoCapitalize='words' returnKeyType='next' onFocus={onFieldFocus} onSubmitEditing={()=>emailRef.current?.focus()} style={[input,compact&&compactInput]}/>:null}
        {mode==='verify'?<Body compact={compact}>Код отправлен на {cleanEmail}</Body>:<TextInput ref={emailRef} value={email} onChangeText={setEmail} placeholder='Email' keyboardType='email-address' autoCapitalize='none' autoCorrect={false} returnKeyType={(mode==='forgot')?'done':'next'} onFocus={onFieldFocus} onSubmitEditing={()=>mode==='forgot'?submit():passwordRef.current?.focus()} style={[input,compact&&compactInput]}/>}
        {(mode==='login'||mode==='register'||mode==='reset')?<TextInput ref={passwordRef} value={password} onChangeText={setPassword} placeholder={mode==='reset'?'Новый пароль (минимум 8 символов)':'Пароль (минимум 8 символов)'} secureTextEntry autoCapitalize='none' returnKeyType={mode==='reset'?'next':'done'} onFocus={onFieldFocus} onSubmitEditing={()=>mode==='reset'?codeRef.current?.focus():submit()} style={[input,compact&&compactInput]}/>:null}
        {(mode==='verify'||mode==='reset')?<TextInput ref={codeRef} value={code} onChangeText={value=>setCode(value.replace(/\D/g,'').slice(0,6))} placeholder='000000' keyboardType='number-pad' maxLength={6} returnKeyType='done' onFocus={onFieldFocus} onSubmitEditing={submit} style={[input,compact&&compactInput,codeInput,compact&&compactCodeInput]}/>:null}
        {mode==='verify'?<Body compact={compact}>{compact?'Код действует 10 минут.':'Код действует 10 минут. Если он истёк, запросите новый.'}</Body>:null}
        {inlineAction}
        {mode==='verify'?<Button compact={compact} secondary disabled={busy||!cleanEmail} title='Отправить код ещё раз' onPress={resend}/>:null}
        {mode==='login'?<><Button compact={compact} secondary title='Создать новый аккаунт' onPress={()=>{Keyboard.dismiss();setMode('register')}}/><Button compact={compact} secondary title='Забыли пароль?' onPress={()=>{Keyboard.dismiss();setMode('forgot')}}/></>:<Button compact={compact} secondary title='Вернуться ко входу' onPress={()=>{Keyboard.dismiss();setMode('login');setCode('')}}/>}
        {!compact?<Body>На другом устройстве установите DOME и войдите с тем же email и паролем.</Body>:null}
      </Card>
    </>}
  </KeyboardAwareForm>
}

const input={borderWidth:1,borderColor:'#CCC',borderRadius:14,padding:14,fontSize:16,marginBottom:12} as const;
const compactInput={borderRadius:9,paddingVertical:6,paddingHorizontal:10,fontSize:14,lineHeight:17,marginBottom:3} as const;
const codeInput={textAlign:'center',fontSize:24,letterSpacing:8} as const;
const compactCodeInput={fontSize:19,lineHeight:22,letterSpacing:6,paddingVertical:4} as const;
