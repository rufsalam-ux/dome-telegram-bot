import React,{useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {ActivityIndicator,Animated,Image,Linking,Pressable,Text,View} from 'react-native';
import {useAudioPlayer} from 'expo-audio';
import {useVideoPlayer,VideoView,type VideoSource} from 'expo-video';

import {lessonMediaSource} from '../api/mobile';
import {mediaPhaseAfterEnd,normalizeMediaSequence,videoStepBehavior,type LessonMediaDescriptor} from '../engine/mediaRuntime';
import {DomePressable} from './DomePressable';

type ResolvedSource={uri:string;headers?:Record<string,string>;useCaching?:boolean};

type VideoOutcome='ended'|'skipped'|'failed';

function VideoStage({source,poster,item,onEnd,onPlaybackChange}:{source:ResolvedSource;poster?:ResolvedSource;item:LessonMediaDescriptor;onEnd:(outcome:VideoOutcome)=>void;onPlaybackChange?:(active:boolean)=>void}){
  const videoSource=source as VideoSource;
  const[ready,setReady]=useState(false);const[ended,setEnded]=useState(false);
  const player=useVideoPlayer(videoSource,current=>{current.loop=false;if(item.autoplay!==false)current.play()});
  useEffect(()=>{const endedSubscription=player.addListener('playToEnd',()=>{setEnded(true);onPlaybackChange?.(false);onEnd('ended')});const statusSubscription=player.addListener('statusChange',(event:any)=>{if(event.status==='readyToPlay'||event.status==='ready')setReady(true);if(event.status==='error'){onPlaybackChange?.(false);onEnd('failed')}});const playingSubscription=player.addListener('playingChange',(event:any)=>onPlaybackChange?.(Boolean(event.isPlaying)));return()=>{endedSubscription.remove();statusSubscription.remove();playingSubscription.remove();onPlaybackChange?.(false)}},[player,onEnd,onPlaybackChange]);
  const replay=()=>{try{player.currentTime=0;setEnded(false);player.play()}catch{onEnd('failed')}};
  return <View style={{width:'100%',height:'100%',backgroundColor:'#111'}}>
    <VideoView testID={`lesson-media-${item.id}`} player={player} nativeControls contentFit='contain' style={{width:'100%',height:'100%'}}/>
    {!ready&&poster?<Image testID='lesson-video-poster' source={poster} resizeMode='contain' style={{position:'absolute',inset:0,width:'100%',height:'100%',backgroundColor:'#111'}}/>:null}
    {!ready&&!poster?<View pointerEvents='none' style={{position:'absolute',inset:0,alignItems:'center',justifyContent:'center'}}><ActivityIndicator color='#fff'/></View>:null}
    {ended&&item.replay!==false?<DomePressable testID='lesson-video-replay' accessibilityRole='button' onPress={replay} style={{position:'absolute',left:14,bottom:14,minHeight:44,minWidth:120,borderRadius:22,backgroundColor:'rgba(0,0,0,.68)'}}><Text style={{color:'#fff',fontWeight:'800'}}>↻ Ещё раз</Text></DomePressable>:null}
    {item.skippable!==false&&!ended?<DomePressable testID='lesson-video-skip' accessibilityRole='button' onPress={()=>{try{player.pause()}catch{}onPlaybackChange?.(false);onEnd('skipped')}} style={{position:'absolute',right:14,top:14,minHeight:44,minWidth:120,borderRadius:22,backgroundColor:'rgba(0,0,0,.68)'}}><Text style={{color:'#fff',fontWeight:'800'}}>Пропустить</Text></DomePressable>:null}
  </View>;
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

export function LessonMediaRuntime({lessonId,slide,minHeight,bundledImage,onVideoDone,onVideoPlaybackChange}:{lessonId:string;slide:any;minHeight:number;bundledImage?:any;onVideoDone?:(outcome:VideoOutcome)=>void;onVideoPlaybackChange?:(active:boolean)=>void}){
  const sequence=useMemo(()=>normalizeMediaSequence(slide),[slide?.slide_id,slide?.media_sequence,slide?.image]);
  const behavior=useMemo(()=>videoStepBehavior(slide),[slide?.slide_id,slide?.media_sequence,slide?.aspect_ratio]);
  const[index,setIndex]=useState(0);const[source,setSource]=useState<ResolvedSource|any>();const[poster,setPoster]=useState<ResolvedSource>();const[loadFailed,setLoadFailed]=useState(false);const item=sequence[index];const doneRef=useRef(onVideoDone);doneRef.current=onVideoDone;
  useEffect(()=>{setIndex(0)},[slide?.slide_id]);
  useEffect(()=>{let active=true;setLoadFailed(false);setPoster(undefined);if(!item){setSource(undefined);return}if(item.type==='image'&&index===0&&bundledImage){setSource(bundledImage);return}const path=String(item.src||item.url||'');if(!path){setSource(undefined);setLoadFailed(true);return}void lessonMediaSource(lessonId,path).then(value=>{if(active)setSource(value)}).catch(()=>{if(active){setSource(undefined);setLoadFailed(true);doneRef.current?.('failed')}});if(item.poster)void lessonMediaSource(lessonId,item.poster).then(value=>{if(active)setPoster(value)}).catch(()=>{});return()=>{active=false}},[lessonId,item?.id,item?.src,item?.url,item?.poster,index,bundledImage]);
  const advance=useCallback((outcome:VideoOutcome='ended')=>{if(item?.advance_on_end!==false)setIndex(current=>mediaPhaseAfterEnd(sequence,current));if(item?.type==='video')doneRef.current?.(outcome)},[item?.advance_on_end,item?.type,sequence]);
  return <View testID='lesson-generic-media-frame' style={{width:'100%',minHeight,flex:1,borderRadius:18,overflow:'hidden',backgroundColor:'#eaf4fb'}}>
    {!item||!source?<View style={{flex:1,alignItems:'center',justifyContent:'center',padding:16}}><Text style={{fontSize:34}}>{loadFailed?'⚠️':'🖼️'}</Text><Text>{loadFailed?'Медиа временно недоступно. Можно продолжить.':'Загружаю медиа…'}</Text></View>:item.type==='image'?<Image testID={`lesson-media-${item.id}`} source={source} resizeMode='contain' style={{width:'100%',height:'100%'}}/>:item.type==='animation'?<AnimatedStage source={source} item={item}/>:item.type==='video'?<VideoStage source={source} poster={poster} item={{...item,...behavior,auto_continue:behavior.autoContinue}} onEnd={advance} onPlaybackChange={onVideoPlaybackChange}/>:item.type==='audio'?<AudioStage source={source} item={item}/>:<Pressable testID={`lesson-media-${item.id}`} accessibilityRole='button' onPress={()=>Linking.openURL(String(item.src||item.url))} style={{flex:1,alignItems:'center',justifyContent:'center',gap:12}}><Text style={{fontSize:48}}>▶️</Text><Text style={{fontSize:18,fontWeight:'800'}}>Открыть видео</Text></Pressable>}
    {sequence.length>1?<View pointerEvents='none' style={{position:'absolute',right:10,top:10,borderRadius:12,backgroundColor:'rgba(0,0,0,.55)',paddingHorizontal:8,paddingVertical:3}}><Text style={{color:'#fff',fontWeight:'800'}}>{index+1}/{sequence.length}</Text></View>:null}
  </View>;
}
