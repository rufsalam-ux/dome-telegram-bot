import React,{useMemo,useRef,useState} from 'react';
import {Animated,Image,Pressable,StyleSheet,Text,View} from 'react-native';

import {lessonShellLayout,type ShellRect} from '../engine/lessonShellLayout';
import {emitDomeFeedback,type DomeFeedbackEvent} from '../experience/useDomeFeedback';

const shellArtwork=require('../../assets/lesson-shell/dome-lesson-shell.jpg');

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

export function LessonPortraitShell({visual,prompt,progressLabel,replay,answer,hint,more,continueAction,overlay}:Props){
  const[size,setSize]=useState({width:360,height:720});const layout=useMemo(()=>lessonShellLayout(size.width,size.height),[size.width,size.height]);
  return <View testID='dome-lesson-portrait-shell' onLayout={event=>{const{width,height}=event.nativeEvent.layout;if(width>0&&height>0&&(Math.abs(width-size.width)>.5||Math.abs(height-size.height)>.5))setSize({width,height})}} style={styles.root}>
    <Image testID='dome-lesson-shell-artwork' source={shellArtwork} resizeMode='contain' style={{position:'absolute',...layout.image}}/>
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
  prompt:{maxHeight:'31%',borderRadius:13,backgroundColor:'rgba(255,250,238,.94)',paddingHorizontal:9,paddingVertical:5,borderWidth:1,borderColor:'rgba(224,174,66,.28)'},
  overlay:{position:'absolute',left:8,right:8,bottom:8,zIndex:30,borderRadius:14,backgroundColor:'rgba(255,255,255,.97)',padding:8},
  hotspot:{flex:1,borderWidth:1.5,shadowColor:'#32210d',shadowOpacity:.18,shadowRadius:3,shadowOffset:{width:0,height:2},elevation:2},
  disabledVeil:{position:'absolute',left:0,right:0,top:0,bottom:0,borderRadius:999,backgroundColor:'rgba(240,240,240,.22)'},
  unavailable:{position:'absolute',borderRadius:999,backgroundColor:'rgba(235,237,239,.46)'},
});
