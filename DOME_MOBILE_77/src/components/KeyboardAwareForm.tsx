import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Keyboard,KeyboardAvoidingView,Platform,ScrollView,TextInputProps,useWindowDimensions,View} from 'react-native';
import {useSafeAreaInsets} from 'react-native-safe-area-context';
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
  const footerHeightRef=useRef(48);
  const insets=useSafeAreaInsets();
  const{height:windowHeight}=useWindowDimensions();
  const fullWindowHeight=useRef(windowHeight);
  const[compact,setCompact]=useState(false);
  const[keyboardHeight,setKeyboardHeight]=useState(0);

  const revealTarget=useCallback((target:unknown,delay:number)=>{
    setTimeout(()=>{
      scrollRef.current?.getScrollResponder()?.scrollResponderScrollNativeHandleToKeyboard(
        target as any,
        compactRef.current?footerHeightRef.current+14:44,
        true,
      );
    },delay);
  },[]);

  useEffect(()=>{
    const shown=Keyboard.addListener('keyboardDidShow',event=>{
      compactRef.current=true;
      setCompact(true);
      setKeyboardHeight(event.endCoordinates.height);
      if(focusedTarget.current!==null){
        revealTarget(focusedTarget.current,30);
        revealTarget(focusedTarget.current,Platform.OS==='android'?180:100);
        if(Platform.OS==='android')revealTarget(focusedTarget.current,360);
      }
    });
    const hidden=Keyboard.addListener('keyboardDidHide',()=>{
      compactRef.current=false;
      setCompact(false);
      setKeyboardHeight(0);
    });
    return()=>{shown.remove();hidden.remove()};
  },[revealTarget]);

  useEffect(()=>{
    if(windowHeight>fullWindowHeight.current)fullWindowHeight.current=windowHeight;
    const resizedForKeyboard=fullWindowHeight.current-windowHeight>120;
    if(resizedForKeyboard&&!compactRef.current){
      compactRef.current=true;
      setCompact(true);
      if(focusedTarget.current!==null){
        revealTarget(focusedTarget.current,80);
        revealTarget(focusedTarget.current,260);
      }
    }else if(!resizedForKeyboard&&compactRef.current&&fullWindowHeight.current-windowHeight<40){
      compactRef.current=false;
      setCompact(false);
    }
  },[windowHeight,revealTarget]);

  const onFieldFocus:NonNullable<TextInputProps['onFocus']>=(event)=>{
    const target=event.target;
    focusedTarget.current=target;
    revealTarget(target,40);
    revealTarget(target,Platform.OS==='android'?240:100);
  };

  const context={compact,onFieldFocus};
  const resizedHeight=Math.max(0,fullWindowHeight.current-windowHeight);
  const uncoveredKeyboardHeight=compact?Math.max(0,keyboardHeight-resizedHeight):0;
  return <KeyboardAvoidingView style={{flex:1}} behavior={Platform.OS==='ios'?'padding':undefined}>
    <View style={{flex:1}}>
      <ScrollView
        ref={scrollRef}
        contentContainerStyle={compact
          ?{flexGrow:1,justifyContent:'flex-start',paddingHorizontal:10,paddingTop:0,paddingBottom:4}
          :{flexGrow:1,justifyContent:'center',paddingHorizontal:24,paddingVertical:32}}
        keyboardShouldPersistTaps='always'
        keyboardDismissMode={Platform.OS==='ios'?'interactive':'on-drag'}
        showsVerticalScrollIndicator={false}
        onContentSizeChange={()=>{
          if(compactRef.current&&focusedTarget.current!==null)revealTarget(focusedTarget.current,40);
        }}
      >
        <View style={{width:'100%',maxWidth,alignSelf:'center'}}>
          {children(context,compact?null:primaryAction(false))}
        </View>
      </ScrollView>
      {compact?<View
        onLayout={event=>{
          const height=event.nativeEvent.layout.height;
          footerHeightRef.current=Math.max(48,height-uncoveredKeyboardHeight);
        }}
        style={{backgroundColor:theme.colors.bg,borderTopWidth:1,borderTopColor:theme.colors.border,paddingHorizontal:10,paddingTop:2,paddingBottom:Math.max(insets.bottom,8)+uncoveredKeyboardHeight,elevation:12}}
      >
        <View style={{width:'100%',maxWidth,alignSelf:'center'}}>{primaryAction(true)}</View>
      </View>:null}
    </View>
  </KeyboardAvoidingView>
}
