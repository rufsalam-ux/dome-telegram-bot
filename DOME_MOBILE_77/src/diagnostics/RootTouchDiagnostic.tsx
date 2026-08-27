import React, {useEffect, useRef, useState} from 'react';
import {Pressable, Text, View, type GestureResponderEvent} from 'react-native';

const BUILD_HASH=process.env.EXPO_PUBLIC_BUILD_COMMIT||'local-uncommitted';

export default function RootTouchDiagnostic(){
  const [globalTapCount,setGlobalTapCount]=useState(0);
  const [buttonPressCount,setButtonPressCount]=useState(0);
  const [coordinates,setCoordinates]=useState({x:0,y:0});
  const [tinted,setTinted]=useState(false);
  const tintTimer=useRef<ReturnType<typeof setTimeout>|null>(null);

  useEffect(()=>()=>{
    if(tintTimer.current)clearTimeout(tintTimer.current);
  },[]);

  const handleGlobalTouch=(event:GestureResponderEvent)=>{
    const {pageX,pageY}=event.nativeEvent;
    setGlobalTapCount(value=>{
      const next=value+1;
      console.log('[DOME_TOUCH] GLOBAL_TOUCH_RECEIVED',{count:next,x:Math.round(pageX),y:Math.round(pageY)});
      return next;
    });
    setCoordinates({x:Math.round(pageX),y:Math.round(pageY)});
    setTinted(true);
    if(tintTimer.current)clearTimeout(tintTimer.current);
    tintTimer.current=setTimeout(()=>setTinted(false),200);
  };

  const handleButtonPress=()=>{
    setButtonPressCount(value=>{
      const next=value+1;
      console.log('[DOME_TOUCH] RETRY_PRESS_RECEIVED',{count:next});
      return next;
    });
  };

  return <View
    testID='root-touch-diagnostic'
    collapsable={false}
    pointerEvents='auto'
    onTouchStart={handleGlobalTouch}
    style={{flex:1,backgroundColor:tinted?'#D8ECFF':'#FFFFFF',paddingHorizontal:24,paddingTop:64,paddingBottom:36,justifyContent:'space-between'}}
  >
    <View collapsable={false}>
      <Text style={{fontSize:30,fontWeight:'900',color:'#102A43'}}>TOUCH TEST</Text>
      <Text style={{marginTop:14,fontSize:16,color:'#334E68'}}>Tap top-left, center, then the blue button.</Text>
      <Text style={{marginTop:20,fontSize:18,fontWeight:'800',color:'#102A43'}}>GLOBAL TAP COUNT: {globalTapCount}</Text>
      <Text style={{marginTop:8,fontSize:18,fontWeight:'800',color:'#102A43'}}>TOUCH X/Y: {coordinates.x} / {coordinates.y}</Text>
      <Text style={{marginTop:8,fontSize:18,fontWeight:'800',color:'#102A43'}}>BUTTON PRESS COUNT: {buttonPressCount}</Text>
      <Text style={{marginTop:20,fontSize:12,color:'#627D98'}}>BUILD HASH: {BUILD_HASH}</Text>
    </View>

    <Pressable
      testID='root-touch-retry-test'
      accessible
      accessibilityRole='button'
      collapsable={false}
      pointerEvents='auto'
      disabled={false}
      hitSlop={12}
      onPress={handleButtonPress}
      style={({pressed})=>({minHeight:64,borderRadius:18,backgroundColor:pressed?'#0958B5':'#0878E8',alignItems:'center',justifyContent:'center',paddingHorizontal:20})}
    >
      <Text style={{fontSize:19,fontWeight:'900',color:'#FFFFFF'}}>RETRY BUTTON TEST</Text>
    </Pressable>
  </View>;
}
