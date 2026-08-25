import React from 'react';
import {Animated,View} from 'react-native';

import type {NormalizedRect} from '../data/lessonInteractions';
import {avatarScaleX,type AvatarFacing} from '../engine/avatarRuntime';

function rectStyle(rect:NormalizedRect|number[]){
  const values=Array.isArray(rect)?{left:rect[0]||0,top:rect[1]||0,width:rect[2]||0,height:rect[3]||0}:rect;
  return {left:`${values.left*100}%`,top:`${values.top*100}%`,width:`${values.width*100}%`,height:`${values.height*100}%`} as any;
}

export function ChildAvatarLayer({uri,rect,facing,animation}:{uri?:string;rect:number[]|null;facing:AvatarFacing;animation:Animated.Value}){
  if(!uri||!rect)return null;
  return <View testID='lesson-hero-overlay' accessibilityLabel='Выбранный герой ребёнка' pointerEvents='none' style={[{position:'absolute',zIndex:5,elevation:5},rectStyle(rect)]}>
    <Animated.Image testID='lesson-child-avatar' source={{uri}} resizeMode='contain' style={{width:'100%',height:'100%',transform:[{translateY:animation.interpolate({inputRange:[0,1],outputRange:[0,-4]})},{scaleX:avatarScaleX(facing)}]}}/>
  </View>;
}
