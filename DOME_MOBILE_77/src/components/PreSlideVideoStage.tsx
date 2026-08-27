import React,{useEffect,useRef,useState} from 'react';
import {ActivityIndicator,Pressable,Text,View} from 'react-native';
import {useVideoPlayer,VideoView,type VideoSource} from 'expo-video';

import {lessonMediaSource} from '../api/mobile';
import type {PreSlideVideoDescriptor} from '../engine/preSlideVideo';

type ResolvedSource={uri:string;headers?:Record<string,string>;useCaching?:boolean};

function Player({source,video,onDone}:{source:ResolvedSource;video:PreSlideVideoDescriptor;onDone:(outcome:'ended'|'failed')=>void}){
  const player=useVideoPlayer(source as VideoSource,current=>{current.loop=false;if(video.autoplay)current.play()});
  useEffect(()=>{const watchdog=setTimeout(()=>onDone('failed'),120_000);const ended=player.addListener('playToEnd',()=>onDone('ended'));const status=player.addListener('statusChange',event=>{if(event.status==='error')onDone('failed')});return()=>{clearTimeout(watchdog);ended.remove();status.remove()}},[player,onDone]);
  return <VideoView testID='pre-slide-video-player' player={player} nativeControls={!video.autoplay} contentFit='contain' style={{width:'100%',height:'100%',backgroundColor:'#000'}}/>;
}

export function PreSlideVideoStage({lessonId,video,onDone}:{lessonId:string;video:PreSlideVideoDescriptor;onDone:(outcome:'ended'|'skipped'|'failed')=>void}){
  const[source,setSource]=useState<ResolvedSource>();const finished=useRef(false);
  const finish=(outcome:'ended'|'skipped'|'failed')=>{if(finished.current)return;finished.current=true;onDone(outcome)};
  useEffect(()=>{let active=true;const timer=setTimeout(()=>finish('failed'),18_000);void lessonMediaSource(lessonId,video.uri).then(value=>{if(active){clearTimeout(timer);setSource(value)}}).catch(()=>finish('failed'));return()=>{active=false;clearTimeout(timer)}},[lessonId,video.uri]);
  return <View testID='pre-slide-video-stage' style={{flex:1,backgroundColor:'#000',alignItems:'center',justifyContent:'center'}}>
    {source?<Player source={source} video={video} onDone={finish}/>:<View style={{alignItems:'center',gap:12}}><ActivityIndicator size='large' color='#fff'/><Text style={{color:'#fff',fontWeight:'700'}}>Загружаю короткое видео…</Text></View>}
    {video.skippable?<Pressable testID='pre-slide-video-skip' accessibilityRole='button' onPress={()=>finish('skipped')} style={{position:'absolute',right:16,top:18,minHeight:44,paddingHorizontal:18,borderRadius:22,backgroundColor:'rgba(0,0,0,.66)',alignItems:'center',justifyContent:'center'}}><Text style={{color:'#fff',fontSize:16,fontWeight:'800'}}>Пропустить</Text></Pressable>:null}
  </View>;
}
