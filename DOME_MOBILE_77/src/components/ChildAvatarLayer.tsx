import React from 'react';
import {Animated,View} from 'react-native';

import type {NormalizedRect} from '../data/lessonInteractions';
import {avatarCanvasStyle,avatarScaleX,sourceAvatarFacing,type AvatarFacing} from '../engine/avatarRuntime';

function rectStyle(rect:NormalizedRect|number[]){
  const values=Array.isArray(rect)?{left:rect[0]||0,top:rect[1]||0,width:rect[2]||0,height:rect[3]||0}:rect;
  return {left:`${values.left*100}%`,top:`${values.top*100}%`,width:`${values.width*100}%`,height:`${values.height*100}%`} as any;
}

export function ChildAvatarLayer({uri,rect,facing,animation,metadata}:{uri?:string;rect:number[]|null;facing:AvatarFacing;animation:Animated.Value;metadata?:any}){
  if(!uri||!rect)return null;
  return <View testID='lesson-hero-overlay' accessibilityLabel='Выбранный герой ребёнка' pointerEvents='none' style={[{position:'absolute',zIndex:5,elevation:5,overflow:'visible'},rectStyle(rect)]}>
    <Animated.Image testID='lesson-child-avatar' source={{uri}} resizeMode='stretch' style={[avatarCanvasStyle(metadata),{transform:[{translateY:animation.interpolate({inputRange:[0,1],outputRange:[0,-4]})},{scaleX:avatarScaleX(facing,sourceAvatarFacing(metadata))}]}]}/>
  </View>;
}
