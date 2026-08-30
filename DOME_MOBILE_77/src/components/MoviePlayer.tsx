import React,{useEffect} from 'react';
import {Text,View} from 'react-native';
import {useVideoPlayer,VideoView,type VideoSource} from 'expo-video';

import type {MovieIdentity} from '../engine/movieRuntime';

export function MoviePlayer({url,identity}:{url:string;identity:MovieIdentity}){
  const source={uri:url,useCaching:true} as VideoSource;
  const player=useVideoPlayer(source,current=>{current.loop=false});
  useEffect(()=>{
    console.info('MOVIE_PLAYER_OPENED',{...identity,movie_url:url});
    const subscription=player.addListener('statusChange',event=>{
      if(event.status==='error')console.error('MOVIE_PLAYER_ERROR',{...identity,movie_url:url,error:String(event.error?.message||event.error||'VIDEO_ERROR')});
    });
    return()=>subscription.remove();
  },[player,url,identity.session_id,identity.run_id,identity.job_id,identity.attempt_id]);
  return <View testID='movie-player' style={{width:'100%',aspectRatio:16/9,borderRadius:16,overflow:'hidden',backgroundColor:'#000',marginVertical:10}}>
    <VideoView player={player} nativeControls contentFit='contain' style={{width:'100%',height:'100%'}}/>
    <Text accessibilityElementsHidden style={{position:'absolute',width:1,height:1,opacity:0}}>movie {identity.session_id}</Text>
  </View>;
}
