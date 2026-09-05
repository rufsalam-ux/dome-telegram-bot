import React,{useEffect,useRef,useState} from 'react';
import {ActivityIndicator,Image,StyleSheet,Text,View} from 'react-native';
import {useVideoPlayer,VideoView,type VideoSource} from 'expo-video';

import {lessonMediaSource} from '../api/mobile';
import type {PreSlideVideoDescriptor} from '../engine/preSlideVideo';
import {DomePressable} from './DomePressable';

type ResolvedSource=any;

// Replay / Skip button style — blends with the fantasy classroom shell
const OVERLAY_BTN = {
  minHeight:44,
  paddingHorizontal:20,
  borderRadius:22,
  backgroundColor:'rgba(15,23,42,0.78)',
  justifyContent:'center' as const,
  alignItems:'center' as const,
  borderWidth:1,
  borderColor:'rgba(56,189,248,0.45)',
};

function Player({source,video,onDone}:{source:ResolvedSource;video:PreSlideVideoDescriptor;onDone:(outcome:'ended'|'failed')=>void}){
  const player=useVideoPlayer(source as VideoSource,current=>{current.loop=false;if(video.autoplay)current.play()});
  useEffect(()=>{
    const watchdog=setTimeout(()=>onDone('failed'),120_000);
    const ended=player.addListener('playToEnd',()=>onDone('ended'));
    const status=player.addListener('statusChange',event=>{if(event.status==='error')onDone('failed')});
    return()=>{clearTimeout(watchdog);ended.remove();status.remove()};
  },[player,onDone]);
  return (
    <View style={{width:'100%',height:'100%',backgroundColor:'#000'}}>
      <VideoView
        testID='pre-slide-video-player'
        player={player}
        nativeControls={false}
        contentFit='contain'
        style={{width:'100%',height:'100%',backgroundColor:'transparent'}}
      />
      {/* Replay button */}
      <View style={{position:'absolute',left:16,bottom:16,flexDirection:'row',gap:12}}>
        <DomePressable
          testID='pre-slide-video-replay'
          accessibilityRole='button'
          onPress={()=>{try{player.currentTime=0;player.play()}catch{try{(player as any).replay()}catch{}}}}
          style={OVERLAY_BTN}
        >
          <Text style={{color:'#e2e8f0',fontSize:14,fontWeight:'700'}}>⏮ Повторить</Text>
        </DomePressable>
      </View>
    </View>
  );
}

export function PreSlideVideoStage({lessonId,video,onDone}:{lessonId:string;video:PreSlideVideoDescriptor;onDone:(outcome:'ended'|'skipped'|'failed')=>void}){
  const[source,setSource]=useState<ResolvedSource>();
  const finished=useRef(false);
  const finish=(outcome:'ended'|'skipped'|'failed')=>{if(finished.current)return;finished.current=true;onDone(outcome)};

  useEffect(()=>{
    let active=true;
    const timer=setTimeout(()=>finish('failed'),18_000);
    void lessonMediaSource(lessonId,video.uri).then(value=>{
      if(active){clearTimeout(timer);setSource(value)}
    }).catch(()=>finish('failed'));
    return()=>{active=false;clearTimeout(timer)};
  },[lessonId,video.uri]);

  return (
    <View testID='pre-slide-video-stage' style={{width:'100%',height:'100%',borderRadius:18,overflow:'hidden',backgroundColor:'#000'}}>
      {source
        ? <Player source={source} video={video} onDone={finish}/>
        : (
          <View style={{flex:1,alignItems:'center',justifyContent:'center',gap:12,backgroundColor:'#0d1b2a'}}>
            <ActivityIndicator size='large' color='#38bdf8'/>
            <Text style={{color:'#cbd5e1',fontWeight:'700',fontSize:14}}>Загружаю видео…</Text>
          </View>
        )
      }

      {/* Skip button in corner of video */}
      {video.skippable
        ? (
          <DomePressable
            testID='pre-slide-video-skip'
            accessibilityRole='button'
            onPress={()=>finish('skipped')}
            style={{...OVERLAY_BTN,position:'absolute',right:12,top:12,minHeight:36,paddingHorizontal:14}}
          >
            <Text style={{color:'#e2e8f0',fontSize:13,fontWeight:'800'}}>Пропустить ›</Text>
          </DomePressable>
        )
        : null
      }
    </View>
  );
}
