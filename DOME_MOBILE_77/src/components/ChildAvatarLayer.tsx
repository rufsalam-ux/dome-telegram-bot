import React,{useEffect,useMemo,useState} from 'react';
import {Animated,View} from 'react-native';

import type {NormalizedRect} from '../data/lessonInteractions';
import {avatarImageFrame,avatarRenderTrace,avatarScaleX,sourceAvatarFacing,type AvatarFacing} from '../engine/avatarRuntime';

function rectStyle(rect:NormalizedRect|number[]){
  const values=Array.isArray(rect)?{left:rect[0]||0,top:rect[1]||0,width:rect[2]||0,height:rect[3]||0}:rect;
  return {left:`${values.left*100}%`,top:`${values.top*100}%`,width:`${values.width*100}%`,height:`${values.height*100}%`} as any;
}

export function ChildAvatarLayer({uri,rect,facing,animation,metadata}:{uri?:string;rect:number[]|null;facing:AvatarFacing;animation:Animated.Value;metadata?:any}){
  const trace=avatarRenderTrace(metadata,facing);const[size,setSize]=useState({width:0,height:0});const frame=useMemo(()=>avatarImageFrame(metadata,size.width,size.height),[metadata,size.width,size.height]);
  useEffect(()=>{if(uri&&rect)console.info('[DOME_AVATAR_RENDER]',JSON.stringify({...trace,rect}))},[uri,rect?.join(','),trace.sourceFacing,trace.desiredFacing,trace.appliedFlip,trace.confirmed,trace.analysisVersion]);
  if(!uri||!rect)return null;
  return <View testID='lesson-hero-overlay' accessibilityLabel='Выбранный герой ребёнка' pointerEvents='none' onLayout={event=>{const{width,height}=event.nativeEvent.layout;if(Math.abs(width-size.width)>.5||Math.abs(height-size.height)>.5)setSize({width,height})}} style={[{position:'absolute',zIndex:5,elevation:5,overflow:'visible'},rectStyle(rect)]}>
    <Animated.Image testID='lesson-child-avatar' source={{uri}} resizeMode='contain' style={[size.width>0&&size.height>0?{position:'absolute',left:frame.left,top:frame.top,width:frame.width,height:frame.height}:{position:'absolute',left:0,top:0,width:'100%',height:'100%'},{transform:[{translateY:animation.interpolate({inputRange:[0,1],outputRange:[0,-4]})},{scaleX:avatarScaleX(facing,sourceAvatarFacing(metadata))}]}]}/>
  </View>;
}
