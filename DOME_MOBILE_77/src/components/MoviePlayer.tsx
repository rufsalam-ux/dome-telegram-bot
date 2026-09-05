import React,{useEffect,useRef} from 'react';
import {Text,View} from 'react-native';
import {useVideoPlayer,VideoView,type VideoSource} from 'expo-video';

import type {MovieIdentity} from '../engine/movieRuntime';

export function MoviePlayer({url,identity}:{url:string;identity:MovieIdentity}){
  const source:VideoSource={uri:url,useCaching:false};
  const player=useVideoPlayer(source,current=>{current.loop=false});
  const diagFiredRef=useRef(false);

  useEffect(()=>{
    diagFiredRef.current=false;
    console.info('MOVIE_PLAYER_OPENED',{...identity,movie_url:url});
    const subscription=player.addListener('statusChange',async event=>{
      if(event.status==='error'){
        const errMsg=String(event.error?.message||event.error||'VIDEO_ERROR');
        console.error('MOVIE_PLAYER_ERROR',{...identity,movie_url:url,error:errMsg});
        // Diagnostic HEAD fetch to check reachability / token validity
        if(!diagFiredRef.current){
          diagFiredRef.current=true;
          try{
            const res=await fetch(url,{method:'HEAD'});
            console.warn('MOVIE_PLAYER_DIAG',{status:res.status,contentType:res.headers.get('content-type'),acceptRanges:res.headers.get('accept-ranges'),contentLength:res.headers.get('content-length'),url});
          }catch(fetchErr){
            console.warn('MOVIE_PLAYER_DIAG_FETCH_FAILED',{error:String((fetchErr as any)?.message||fetchErr),url});
          }
        }
      }
    });
    return()=>subscription.remove();
  },[player,url,identity.session_id,identity.run_id,identity.job_id,identity.attempt_id]);

  return <View testID='movie-player' style={{width:'100%',aspectRatio:16/9,borderRadius:16,overflow:'hidden',backgroundColor:'#000',marginVertical:10}}>
    <VideoView player={player} nativeControls contentFit='contain' style={{width:'100%',height:'100%'}}/>
    <Text accessibilityElementsHidden style={{position:'absolute',width:1,height:1,opacity:0}}>movie {identity.session_id}</Text>
  </View>;
}
