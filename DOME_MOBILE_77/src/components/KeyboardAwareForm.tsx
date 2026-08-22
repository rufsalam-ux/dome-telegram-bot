import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Keyboard,KeyboardAvoidingView,Platform,ScrollView,TextInputProps,useWindowDimensions,View} from 'react-native';
import {theme} from '../theme/theme';

export type KeyboardFormContext={
  compact:boolean;
  onFieldFocus:NonNullable<TextInputProps['onFocus']>;
};

type Props={
  children:(context:KeyboardFormContext,inlinePrimaryAction:React.ReactNode)=>React.ReactNode;
  primaryAction:(compact:boolean)=>React.ReactNode;
  maxWidth?:number;
};

export function KeyboardAwareForm({children,primaryAction,maxWidth=520}:Props){
  const scrollRef=useRef<ScrollView>(null);
  const focusedTarget=useRef<unknown|null>(null);
  const compactRef=useRef(false);
  const{height:windowHeight}=useWindowDimensions();
  const fullWindowHeight=useRef(windowHeight);
  const[compact,setCompact]=useState(false);

  const revealTarget=useCallback((target:unknown,delay:number)=>{
    setTimeout(()=>{
      scrollRef.current?.getScrollResponder()?.scrollResponderScrollNativeHandleToKeyboard(
        target as any,
        compactRef.current?76:40,
        true,
      );
    },delay);
  },[]);

  useEffect(()=>{
    const shown=Keyboard.addListener('keyboardDidShow',()=>{
      compactRef.current=true;
      setCompact(true);
      if(focusedTarget.current!==null){
        revealTarget(focusedTarget.current,40);
        if(Platform.OS==='android')revealTarget(focusedTarget.current,220);
      }
    });
    const hidden=Keyboard.addListener('keyboardDidHide',()=>{
      compactRef.current=false;
      setCompact(false);
    });
    return()=>{shown.remove();hidden.remove()};
  },[revealTarget]);

  useEffect(()=>{
    if(windowHeight>fullWindowHeight.current)fullWindowHeight.current=windowHeight;
    const resizedForKeyboard=fullWindowHeight.current-windowHeight>120;
    if(resizedForKeyboard&&!compactRef.current){
      compactRef.current=true;
      setCompact(true);
      if(focusedTarget.current!==null)revealTarget(focusedTarget.current,80);
    }else if(!resizedForKeyboard&&compactRef.current&&fullWindowHeight.current-windowHeight<40){
      compactRef.current=false;
      setCompact(false);
    }
  },[windowHeight,revealTarget]);

  const onFieldFocus:NonNullable<TextInputProps['onFocus']>=(event)=>{
    const target=event.target;
    focusedTarget.current=target;
    revealTarget(target,Platform.OS==='android'?220:80);
  };

  const context={compact,onFieldFocus};
  return <KeyboardAvoidingView style={{flex:1}} behavior={Platform.OS==='ios'?'padding':undefined}>
    <View style={{flex:1}}>
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={compact
          ?{flexGrow:1,justifyContent:'flex-start',paddingHorizontal:16,paddingTop:4,paddingBottom:12}
          :{flexGrow:1,justifyContent:'center',paddingHorizontal:24,paddingVertical:32}}
        keyboardShouldPersistTaps='handled'
        keyboardDismissMode={Platform.OS==='ios'?'interactive':'on-drag'}
        showsVerticalScrollIndicator={false}
      >
        <View style={{width:'100%',maxWidth,alignSelf:'center'}}>
          {children(context,compact?null:primaryAction(false))}
        </View>
      </ScrollView>
      {compact?<View style={{backgroundColor:theme.colors.bg,borderTopWidth:1,borderTopColor:theme.colors.border,paddingHorizontal:16,paddingTop:5,paddingBottom:7}}>
        <View style={{width:'100%',maxWidth,alignSelf:'center'}}>{primaryAction(true)}</View>
      </View>:null}
    </View>
  </KeyboardAvoidingView>
}
