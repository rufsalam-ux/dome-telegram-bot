import React,{useEffect,useMemo,useRef,useState} from 'react';
import {Animated,Image,Linking,Pressable,Text,View} from 'react-native';
import {useAudioPlayer} from 'expo-audio';
import {useVideoPlayer,VideoView,type VideoSource} from 'expo-video';

import {lessonMediaSource} from '../api/mobile';
import {mediaPhaseAfterEnd,normalizeMediaSequence,type LessonMediaDescriptor} from '../engine/mediaRuntime';

type ResolvedSource={uri:string;headers?:Record<string,string>;useCaching?:boolean};

function VideoStage({source,item,onEnd}:{source:ResolvedSource;item:LessonMediaDescriptor;onEnd:()=>void}){
  const videoSource=source as VideoSource;
  const player=useVideoPlayer(videoSource,current=>{current.loop=false;if(item.autoplay)current.play()});
  useEffect(()=>{const subscription=player.addListener('playToEnd',onEnd);return()=>subscription.remove()},[player,onEnd]);
  return <VideoView testID={`lesson-media-${item.id}`} player={player} nativeControls contentFit='contain' style={{width:'100%',height:'100%',backgroundColor:'#111'}}/>;
}

function AudioStage({source,item}:{source:ResolvedSource;item:LessonMediaDescriptor}){
  const player=useAudioPlayer(source);
  return <View testID={`lesson-media-${item.id}`} style={{flex:1,alignItems:'center',justifyContent:'center',gap:12}}><Text style={{fontSize:48}}>🎧</Text><Pressable accessibilityRole='button' onPress={()=>{try{player.seekTo(0);player.play()}catch{}}} style={{minHeight:52,minWidth:180,borderRadius:18,backgroundColor:'#246bfd',alignItems:'center',justifyContent:'center'}}><Text style={{color:'#fff',fontSize:18,fontWeight:'800'}}>▶ Послушать</Text></Pressable></View>;
}

function AnimatedStage({source,item}:{source:ResolvedSource;item:LessonMediaDescriptor}){
  const motion=useRef(new Animated.Value(0)).current;
  useEffect(()=>{const loop=Animated.loop(Animated.sequence([Animated.timing(motion,{toValue:1,duration:850,useNativeDriver:true}),Animated.timing(motion,{toValue:0,duration:850,useNativeDriver:true})]));loop.start();return()=>loop.stop()},[motion]);
  return <Animated.Image testID={`lesson-media-${item.id}`} source={source} resizeMode='contain' style={{width:'100%',height:'100%',transform:[{translateY:motion.interpolate({inputRange:[0,1],outputRange:[0,-8]})},{scale:motion.interpolate({inputRange:[0,1],outputRange:[1,1.025]})}]}}/>;
}

export function LessonMediaRuntime({lessonId,slide,minHeight,bundledImage}:{lessonId:string;slide:any;minHeight:number;bundledImage?:any}){
  const sequence=useMemo(()=>normalizeMediaSequence(slide),[slide?.slide_id,slide?.media_sequence,slide?.image]);
  const[index,setIndex]=useState(0);const[source,setSource]=useState<ResolvedSource|any>();const item=sequence[index];
  useEffect(()=>{setIndex(0)},[slide?.slide_id]);
  useEffect(()=>{let active=true;if(!item){setSource(undefined);return}if(item.type==='image'&&index===0&&bundledImage){setSource(bundledImage);return}const path=String(item.src||item.url||'');if(!path){setSource(undefined);return}void lessonMediaSource(lessonId,path).then(value=>{if(active)setSource(value)}).catch(()=>{if(active)setSource(undefined)});return()=>{active=false}},[lessonId,item?.id,item?.src,item?.url,index,bundledImage]);
  const advance=()=>{if(item?.advance_on_end!==false)setIndex(current=>mediaPhaseAfterEnd(sequence,current))};
  return <View testID='lesson-generic-media-frame' style={{width:'100%',minHeight,aspectRatio:16/9,borderRadius:18,overflow:'hidden',backgroundColor:'#eaf4fb'}}>
    {!item||!source?<View style={{flex:1,alignItems:'center',justifyContent:'center'}}><Text style={{fontSize:34}}>🖼️</Text><Text>Загружаю медиа…</Text></View>:item.type==='image'?<Image testID={`lesson-media-${item.id}`} source={source} resizeMode='contain' style={{width:'100%',height:'100%'}}/>:item.type==='animation'?<AnimatedStage source={source} item={item}/>:item.type==='video'?<VideoStage source={source} item={item} onEnd={advance}/>:item.type==='audio'?<AudioStage source={source} item={item}/>:<Pressable testID={`lesson-media-${item.id}`} accessibilityRole='button' onPress={()=>Linking.openURL(String(item.src||item.url))} style={{flex:1,alignItems:'center',justifyContent:'center',gap:12}}><Text style={{fontSize:48}}>▶️</Text><Text style={{fontSize:18,fontWeight:'800'}}>Открыть видео</Text></Pressable>}
    {sequence.length>1?<View pointerEvents='none' style={{position:'absolute',right:10,top:10,borderRadius:12,backgroundColor:'rgba(0,0,0,.55)',paddingHorizontal:8,paddingVertical:3}}><Text style={{color:'#fff',fontWeight:'800'}}>{index+1}/{sequence.length}</Text></View>:null}
  </View>;
}
