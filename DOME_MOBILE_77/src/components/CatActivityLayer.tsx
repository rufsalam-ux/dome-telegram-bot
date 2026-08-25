import React,{useEffect,useRef,useState} from 'react';
import {Animated,Image,Pressable,Text,Vibration,View} from 'react-native';
import {useAudioPlayer} from 'expo-audio';

import {catStateForStage,type CatActivityState} from '../engine/catRuntime';
import type {RuntimeStage} from '../engine/lessonRuntime';

export function CatActivityLayer({stage,compact=false,dragging=false}:{stage:RuntimeStage;compact?:boolean;dragging?:boolean}){
  const[activity,setActivity]=useState<CatActivityState>(()=>catStateForStage(stage));const[stars,setStars]=useState(0);const[gameActive,setGameActive]=useState(false);const bob=useRef(new Animated.Value(0)).current;const successSound=useAudioPlayer(require('../../assets/sounds/suitcase-pop.wav'));
  useEffect(()=>{let idleTimer:any;let gameTimer:any;if(stage==='AI_SPEAKING'){setActivity('listening');return}if(stage==='PROCESSING'){if(gameActive){setActivity('playing');return}setActivity('thinking');idleTimer=setTimeout(()=>setActivity('idle'),1500);gameTimer=setTimeout(()=>{setGameActive(true);setActivity('playing')},4000)}else setActivity(gameActive?'playing':catStateForStage(stage));return()=>{if(idleTimer)clearTimeout(idleTimer);if(gameTimer)clearTimeout(gameTimer)}},[stage,gameActive]);
  useEffect(()=>{const animated=!dragging&&activity!=='listening'&&activity!=='sleeping'&&activity!=='thinking';if(!animated){bob.stopAnimation();bob.setValue(0);return}const loop=Animated.loop(Animated.sequence([Animated.timing(bob,{toValue:1,duration:700,useNativeDriver:true}),Animated.timing(bob,{toValue:0,duration:700,useNativeDriver:true})]));loop.start();return()=>loop.stop()},[activity,dragging,bob]);
  const catchStar=()=>{Vibration.vibrate(8);try{successSound.seekTo(0);successSound.play()}catch{}setStars(value=>value+1);setActivity('happy');setTimeout(()=>{if(stage!=='AI_SPEAKING')setActivity('playing')},700)};
  const height=compact?40:50;return <View testID='dome-cat-layer' accessibilityLabel={`DOME cat ${activity}`} style={{height,flexDirection:'row',alignItems:'center',justifyContent:'center',overflow:'hidden',opacity:dragging?.6:1}}>
    <Pressable testID='dome-cat-touch' accessibilityRole='button' accessibilityLabel='Погладить кота DOME' disabled={activity==='listening'} onPress={catchStar} hitSlop={8}><Animated.Image source={require('../../assets/heroes/cat.png')} resizeMode='contain' style={{width:compact?38:48,height:height-2,transform:[{translateY:bob.interpolate({inputRange:[0,1],outputRange:[0,-3]})},{rotate:activity==='surprised'?'4deg':'0deg'}]}}/></Pressable>
    {activity==='playing'?<Pressable testID='cat-mini-game-star' accessibilityRole='button' accessibilityLabel='Поймать звезду с котом' onPress={catchStar} hitSlop={12} style={{minWidth:48,minHeight:38,alignItems:'center',justifyContent:'center'}}><Image source={require('../../assets/heroes/star.png')} resizeMode='contain' style={{width:compact?28:34,height:compact?28:34}}/></Pressable>:null}
    {stars?<Text accessibilityLabel={`Cat stars ${stars}`} style={{fontWeight:'900',color:'#765400'}}>×{stars}</Text>:null}
  </View>;
}
