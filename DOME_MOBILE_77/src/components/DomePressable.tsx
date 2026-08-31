import React,{useRef,useState} from 'react';
import {Animated,Pressable,type PressableProps,type StyleProp,type ViewStyle} from 'react-native';
import {emitDomeFeedback,type DomeFeedbackEvent} from '../experience/useDomeFeedback';

type Props=Omit<PressableProps,'style'|'children'> & {
  children:React.ReactNode;
  style?:StyleProp<ViewStyle>;
  contentStyle?:StyleProp<ViewStyle>;
  pressedStyle?:StyleProp<ViewStyle>;
  feedback?:DomeFeedbackEvent|false;
};

export function DomePressable({children,style,contentStyle,pressedStyle,feedback=false,onPressIn,onPressOut,disabled,...props}:Props){
  const motion=useRef(new Animated.Value(0)).current;const[pressed,setPressed]=useState(false);
  const pressIn=(event:any)=>{setPressed(true);Animated.timing(motion,{toValue:1,duration:72,useNativeDriver:true}).start();if(feedback)emitDomeFeedback(feedback);onPressIn?.(event)};
  const pressOut=(event:any)=>{setPressed(false);Animated.spring(motion,{toValue:0,speed:28,bounciness:6,useNativeDriver:true}).start();onPressOut?.(event)};
  return <Animated.View style={[style,pressed&&pressedStyle,{transform:[{translateY:motion.interpolate({inputRange:[0,1],outputRange:[0,2]})},{scale:motion.interpolate({inputRange:[0,1],outputRange:[1,.955]})}]}]}>
    <Pressable {...props} disabled={disabled} onPressIn={pressIn} onPressOut={pressOut} style={[{flex:1,alignItems:'center',justifyContent:'center'},contentStyle]}>{children}</Pressable>
  </Animated.View>;
}
