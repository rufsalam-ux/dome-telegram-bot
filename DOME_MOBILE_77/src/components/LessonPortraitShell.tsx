import React,{useEffect,useMemo,useRef,useState} from 'react';
import {AccessibilityInfo,Animated,Image,Pressable,StyleSheet,Text,View} from 'react-native';

import {lessonShellLayout,type ShellRect} from '../engine/lessonShellLayout';
import {emitDomeFeedback,type DomeFeedbackEvent} from '../experience/useDomeFeedback';

const shellArtwork=require('../../assets/lesson-shell/dome-lesson-shell-extended-v1.png');
const SHELL_SOURCE={width:841,height:1870};

type ShellAction={
  accessibilityLabel:string;
  disabled?:boolean;
  feedback?:DomeFeedbackEvent;
  sound?:boolean;
  onPress:()=>void;
  testID:string;
};

type Props={
  visual:React.ReactNode;
  prompt:React.ReactNode;
  progressLabel:string;
  replay:ShellAction;
  answer?:ShellAction;
  hint?:ShellAction;
  more:ShellAction;
  continueAction:ShellAction;
  overlay?:React.ReactNode;
};

function PhysicalHotspot({rect,action,shape='round',accent}:{rect:ShellRect;action:ShellAction;shape?:'round'|'wide';accent:string}){
  const press=useRef(new Animated.Value(0)).current;const[pressed,setPressed]=useState(false);
  const animate=(toValue:number)=>Animated.timing(press,{toValue,duration:toValue?95:135,useNativeDriver:true}).start();
  const release=()=>{setPressed(false);Animated.spring(press,{toValue:0,speed:24,bounciness:7,useNativeDriver:true}).start()};
  return <Animated.View pointerEvents='box-none' style={{position:'absolute',...rect,transform:[{translateY:press.interpolate({inputRange:[0,1],outputRange:[0,Math.max(2,rect.height*.025)]})},{scale:press.interpolate({inputRange:[0,1],outputRange:[1,.945]})}]}}>
    <Pressable
      accessibilityRole='button'
      accessibilityLabel={action.accessibilityLabel}
      accessibilityState={{disabled:Boolean(action.disabled)}}
      testID={action.testID}
      disabled={Boolean(action.disabled)}
      onPress={action.onPress}
      onPressIn={()=>{setPressed(true);animate(1);emitDomeFeedback(action.feedback||'tap',{sound:action.sound!==false})}}
      onPressOut={release}
      style={[styles.hotspot,{borderRadius:shape==='wide'?rect.height/2:Math.min(rect.width,rect.height)/2,backgroundColor:action.disabled?'rgba(235,237,239,.48)':pressed?'rgba(255,255,255,.22)':'rgba(255,255,255,.015)',borderColor:action.disabled?'rgba(120,120,120,.18)':`${accent}33`}]}>
      {action.disabled?<View pointerEvents='none' style={styles.disabledVeil}/>:null}
    </Pressable>
  </Animated.View>;
}

function EnvironmentMotionLayer({image,scale}:{image:ShellRect;scale:number}){
  const motion=useRef(new Animated.Value(0)).current;const blink=useRef(new Animated.Value(0)).current;const[reduceMotion,setReduceMotion]=useState(true);
  useEffect(()=>{
    let mounted=true;void AccessibilityInfo.isReduceMotionEnabled().then(value=>{if(mounted)setReduceMotion(value)}).catch(()=>{});
    const subscription=AccessibilityInfo.addEventListener('reduceMotionChanged',setReduceMotion);
    return()=>{mounted=false;subscription.remove()};
  },[]);
  useEffect(()=>{
    motion.stopAnimation();blink.stopAnimation();motion.setValue(0);blink.setValue(0);if(reduceMotion)return;
    const animation=Animated.loop(Animated.sequence([
      Animated.timing(motion,{toValue:1,duration:2600,useNativeDriver:true}),
      Animated.timing(motion,{toValue:0,duration:2600,useNativeDriver:true}),
    ]));
    const blinking=Animated.loop(Animated.sequence([Animated.delay(3600),Animated.timing(blink,{toValue:1,duration:75,useNativeDriver:true}),Animated.timing(blink,{toValue:0,duration:110,useNativeDriver:true}),Animated.delay(1700)]));
    animation.start();blinking.start();return()=>{animation.stop();blinking.stop()};
  },[motion,blink,reduceMotion]);
  if(reduceMotion)return null;
  return <View pointerEvents='none' testID='dome-environment-motion' style={StyleSheet.absoluteFill}>
    <Animated.View testID='dome-balloon-motion' style={{position:'absolute',left:image.left+522*scale,top:image.top+356*scale,width:94*scale,height:142*scale,overflow:'hidden',transform:[{translateX:motion.interpolate({inputRange:[0,1],outputRange:[-2*scale,3*scale]})},{translateY:motion.interpolate({inputRange:[0,1],outputRange:[2*scale,-5*scale]})}]}}><Image source={shellArtwork} style={{position:'absolute',left:-522*scale,top:-356*scale,width:SHELL_SOURCE.width*scale,height:SHELL_SOURCE.height*scale}}/></Animated.View>
    <Animated.View testID='dome-cat-breathing' style={{position:'absolute',left:image.left,top:image.top+1038*scale,width:270*scale,height:360*scale,overflow:'hidden',transform:[{translateY:motion.interpolate({inputRange:[0,1],outputRange:[1.5*scale,-2*scale]})},{scale:motion.interpolate({inputRange:[0,1],outputRange:[1,1.006]})}]}}><Image source={shellArtwork} style={{position:'absolute',left:0,top:-1038*scale,width:SHELL_SOURCE.width*scale,height:SHELL_SOURCE.height*scale}}/></Animated.View>
    <Animated.View testID='dome-cat-blink-left' style={[styles.catBlink,{left:image.left+117*scale,top:image.top+1138*scale,width:25*scale,height:5*scale,opacity:blink,transform:[{scaleY:blink.interpolate({inputRange:[0,1],outputRange:[.2,1]})}]}]}/>
    <Animated.View testID='dome-cat-blink-right' style={[styles.catBlink,{left:image.left+181*scale,top:image.top+1132*scale,width:25*scale,height:5*scale,opacity:blink,transform:[{scaleY:blink.interpolate({inputRange:[0,1],outputRange:[.2,1]})}]}]}/>
    <Animated.View style={[styles.skyGlow,{left:image.left+548*scale,top:image.top+372*scale,width:82*scale,height:82*scale,opacity:motion.interpolate({inputRange:[0,1],outputRange:[.08,.22]}),transform:[{scale:motion.interpolate({inputRange:[0,1],outputRange:[.94,1.06]})}]}]}/>
    <Animated.View style={[styles.catSparkle,{left:image.left+91*scale,top:image.top+1090*scale,width:15*scale,height:15*scale,opacity:motion.interpolate({inputRange:[0,1],outputRange:[.24,.76]}),transform:[{translateY:motion.interpolate({inputRange:[0,1],outputRange:[2*scale,-3*scale]})},{rotate:motion.interpolate({inputRange:[0,1],outputRange:['0deg','18deg']})}]}]}/>
  </View>;
}

export function LessonPortraitShell({visual,prompt,progressLabel,replay,answer,hint,more,continueAction,overlay}:Props){
  const[size,setSize]=useState({width:360,height:720});const layout=useMemo(()=>lessonShellLayout(size.width,size.height),[size.width,size.height]);
  return <View testID='dome-lesson-portrait-shell' onLayout={event=>{const{width,height}=event.nativeEvent.layout;if(width>0&&height>0&&(Math.abs(width-size.width)>.5||Math.abs(height-size.height)>.5))setSize({width,height})}} style={styles.root}>
    <Image testID='dome-lesson-shell-artwork' source={shellArtwork} resizeMode='contain' style={{position:'absolute',...layout.image}}/>
    <EnvironmentMotionLayer image={layout.image} scale={layout.scale}/>
    <View testID='dome-lesson-central-panel' style={[styles.panel,layout.content]}>
      <View style={styles.progressPill}><Text style={styles.progressText}>{progressLabel}</Text></View>
      <View style={styles.visual}>{visual}</View>
      <View style={styles.prompt}>{prompt}</View>
      {overlay?<View style={styles.overlay}>{overlay}</View>:null}
    </View>
    <PhysicalHotspot rect={layout.controls.replay} action={replay} accent='#2d83ca'/>
    {answer?<PhysicalHotspot rect={layout.controls.answer} action={answer} accent='#66b820'/>:<View pointerEvents='none' style={[styles.unavailable,layout.controls.answer]}/>} 
    {hint?<PhysicalHotspot rect={layout.controls.hint} action={hint} accent='#efad21'/>:<View pointerEvents='none' style={[styles.unavailable,layout.controls.hint]}/>} 
    <PhysicalHotspot rect={layout.controls.more} action={more} accent='#8d54c5'/>
    <PhysicalHotspot rect={layout.controls.continue} action={continueAction} shape='wide' accent='#2d83ca'/>
  </View>;
}

const styles=StyleSheet.create({
  root:{flex:1,overflow:'hidden',backgroundColor:'#bce8ff'},
  panel:{position:'absolute',overflow:'hidden',borderRadius:26,padding:7},
  progressPill:{position:'absolute',right:9,top:7,zIndex:20,borderRadius:12,backgroundColor:'rgba(255,255,255,.88)',paddingHorizontal:8,paddingVertical:3},
  progressText:{fontSize:11,fontWeight:'800',color:'#52606d'},
  visual:{flex:1,minHeight:0,paddingTop:2},
  prompt:{alignSelf:'flex-end',width:'78%',maxHeight:'27%',marginBottom:'4%',borderRadius:13,backgroundColor:'rgba(255,250,238,.94)',paddingHorizontal:9,paddingVertical:5,borderWidth:1,borderColor:'rgba(224,174,66,.28)'},
  overlay:{position:'absolute',left:8,right:8,bottom:8,zIndex:30,borderRadius:14,backgroundColor:'rgba(255,255,255,.97)',padding:8},
  hotspot:{flex:1,borderWidth:1.5,shadowColor:'#32210d',shadowOpacity:.18,shadowRadius:3,shadowOffset:{width:0,height:2},elevation:2},
  disabledVeil:{position:'absolute',left:0,right:0,top:0,bottom:0,borderRadius:999,backgroundColor:'rgba(240,240,240,.22)'},
  unavailable:{position:'absolute',borderRadius:999,backgroundColor:'rgba(235,237,239,.46)'},
  skyGlow:{position:'absolute',borderRadius:999,backgroundColor:'rgba(255,235,135,.72)'},
  catSparkle:{position:'absolute',borderRadius:4,backgroundColor:'rgba(255,248,181,.92)',shadowColor:'#fff',shadowOpacity:.8,shadowRadius:5,elevation:2},
  catBlink:{position:'absolute',zIndex:6,borderRadius:999,backgroundColor:'rgba(45,31,22,.78)'},
});
