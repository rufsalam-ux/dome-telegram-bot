import React,{useEffect,useRef,useState} from 'react';
import {Animated,Pressable,View} from 'react-native';

import {catStateForStage,type CatActivityState} from '../engine/catRuntime';
import type {RuntimeStage} from '../engine/lessonRuntime';

export function CatActivityLayer({stage,compact=false,dragging=false}:{stage:RuntimeStage;compact?:boolean;dragging?:boolean}){
  const[activity,setActivity]=useState<CatActivityState>(()=>catStateForStage(stage));const bob=useRef(new Animated.Value(0)).current;
  useEffect(()=>{let idleTimer:any;let waitingTimer:any;if(stage==='AI_SPEAKING'){setActivity('listening');return}if(stage==='PROCESSING'){setActivity('thinking');idleTimer=setTimeout(()=>setActivity('idle'),1500);waitingTimer=setTimeout(()=>setActivity('waiting'),4000)}else setActivity(catStateForStage(stage));return()=>{if(idleTimer)clearTimeout(idleTimer);if(waitingTimer)clearTimeout(waitingTimer)}},[stage]);
  useEffect(()=>{const animated=!dragging&&activity!=='listening'&&activity!=='sleeping'&&activity!=='thinking';if(!animated){bob.stopAnimation();bob.setValue(0);return}const loop=Animated.loop(Animated.sequence([Animated.timing(bob,{toValue:1,duration:700,useNativeDriver:true}),Animated.timing(bob,{toValue:0,duration:700,useNativeDriver:true})]));loop.start();return()=>loop.stop()},[activity,dragging,bob]);
  const greetCat=()=>{setActivity('happy');setTimeout(()=>setActivity(catStateForStage(stage)),700)};
  const height=compact?54:70;return <View testID='dome-cat-layer' accessibilityLabel={`DOME cat ${activity}`} style={{height,flexDirection:'row',alignItems:'center',justifyContent:'center',overflow:'hidden',opacity:dragging?.6:1}}>
    <Pressable testID='dome-cat-touch' accessibilityRole='button' accessibilityLabel='Погладить кота DOME' disabled={activity==='listening'} onPress={greetCat} hitSlop={8}><Animated.Image source={require('../../assets/heroes/cat.png')} resizeMode='contain' style={{width:compact?52:68,height:height-2,transform:[{translateY:bob.interpolate({inputRange:[0,1],outputRange:[0,-3]})},{rotate:activity==='surprised'?'4deg':'0deg'}]}}/></Pressable>
  </View>;
}
