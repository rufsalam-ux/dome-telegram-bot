import React,{useEffect,useRef,useState} from 'react';
import {Animated,Pressable,Vibration,View} from 'react-native';

import {catStateForStage,type CatActivityState} from '../engine/catRuntime';
import type {RuntimeStage} from '../engine/lessonRuntime';

export function CatActivityLayer({stage,compact=false,dragging=false}:{stage:RuntimeStage;compact?:boolean;dragging?:boolean}){
  const[activity,setActivity]=useState<CatActivityState>(()=>catStateForStage(stage));const bob=useRef(new Animated.Value(0)).current;
  useEffect(()=>{let idleTimer:any;let waitingTimer:any;if(stage==='AI_SPEAKING'){setActivity('listening');return}if(stage==='PROCESSING'){setActivity('thinking');idleTimer=setTimeout(()=>setActivity('idle'),1500);waitingTimer=setTimeout(()=>setActivity('waiting'),4000)}else setActivity(catStateForStage(stage));return()=>{if(idleTimer)clearTimeout(idleTimer);if(waitingTimer)clearTimeout(waitingTimer)}},[stage]);
  useEffect(()=>{const animated=!dragging&&activity!=='listening'&&activity!=='sleeping'&&activity!=='thinking';if(!animated){bob.stopAnimation();bob.setValue(0);return}const loop=Animated.loop(Animated.sequence([Animated.timing(bob,{toValue:1,duration:700,useNativeDriver:true}),Animated.timing(bob,{toValue:0,duration:700,useNativeDriver:true})]));loop.start();return()=>loop.stop()},[activity,dragging,bob]);
  const greetCat=()=>{Vibration.vibrate(8);setActivity('happy');setTimeout(()=>setActivity(catStateForStage(stage)),700)};
  const focused=stage==='AI_SPEAKING'||stage==='PROCESSING';const height=focused?(compact?54:70):0;return <View testID='dome-cat-layer' accessibilityLabel={`DOME cat ${activity}`} style={{height,zIndex:10,flexDirection:'row',alignItems:'center',justifyContent:'center',overflow:'visible',opacity:dragging?.35:1}}>
    <Pressable testID='dome-cat-touch' accessibilityRole='button' accessibilityLabel='Погладить кота DOME' disabled={activity==='listening'||dragging} onPress={greetCat} hitSlop={8} style={focused?undefined:{position:'absolute',right:4,top:compact?-42:-52}}><Animated.Image source={require('../../assets/branding/dome-splash-v2.png')} resizeMode='contain' style={{width:focused?(compact?52:68):(compact?38:48),height:focused?height-2:(compact?38:48),transform:[{translateY:bob.interpolate({inputRange:[0,1],outputRange:[0,-3]})},{rotate:activity==='surprised'?'4deg':'0deg'}]}}/></Pressable>
  </View>;
}
