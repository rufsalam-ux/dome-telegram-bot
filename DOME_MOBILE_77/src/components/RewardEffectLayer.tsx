import React,{useEffect,useRef} from 'react';
import {Animated,Image,View} from 'react-native';

export function RewardEffectLayer({rewardNonce}:{rewardNonce:number}){
  const progress=useRef(new Animated.Value(0)).current;
  useEffect(()=>{if(rewardNonce<=0)return;progress.stopAnimation();progress.setValue(0);Animated.sequence([Animated.timing(progress,{toValue:1,duration:180,useNativeDriver:true}),Animated.delay(260),Animated.timing(progress,{toValue:0,duration:260,useNativeDriver:true})]).start()},[rewardNonce,progress]);
  if(rewardNonce<=0)return null;
  return <View testID='lesson-reward-effect' pointerEvents='none' style={{position:'absolute',inset:0,zIndex:30,elevation:30,alignItems:'center',justifyContent:'center'}}>
    <Animated.View style={{opacity:progress,transform:[{scale:progress.interpolate({inputRange:[0,1],outputRange:[.45,1.15]})}]}}><Image source={require('../../assets/heroes/star.png')} resizeMode='contain' style={{width:88,height:88}}/></Animated.View>
  </View>;
}
