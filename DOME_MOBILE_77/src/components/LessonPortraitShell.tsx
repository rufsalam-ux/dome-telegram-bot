import React,{useEffect,useMemo,useRef,useState} from 'react';
import {AccessibilityInfo,Animated,Image,Pressable,StyleSheet,Text,View} from 'react-native';

import {lessonShellLayout,type ShellRect} from '../engine/lessonShellLayout';
import {emitDomeFeedback,type DomeFeedbackEvent} from '../experience/useDomeFeedback';
import {DomeMascot} from './DomeMascot';
import type {MascotState} from '../mascot/mascotRegistry';

// The static scene intentionally contains no mascot. DomeMascot below is the
// sole dynamic mascot layer, so there can never be a second baked-in cat.
const shellArtwork=require('../../assets/lesson-shell/dome-lesson-shell-extended-clean-v2.png');
const SHELL_SOURCE={width:841,height:1870};

type ShellAction={
  accessibilityLabel:string;
  disabled?:boolean;
  /** Only the next action may breathe; secondary controls stay calm. */
  primary?:boolean;
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
  /** Kept outside the clipped prompt so a saved child take is always reachable. */
  recordingTools?:React.ReactNode;
  mascotState?:MascotState;
};

function PhysicalHotspot({rect,action,shape='round',accent}:{rect:ShellRect;action:ShellAction;shape?:'round'|'wide';accent?:string}){
  const press=useRef(new Animated.Value(0)).current;
  const pulse=useRef(new Animated.Value(0)).current;
  const isDisabled=Boolean(action.disabled);

  useEffect(()=>{
    if(isDisabled||!action.primary){
      pulse.stopAnimation();
      pulse.setValue(0);
      return;
    }
    const loop=Animated.loop(
      Animated.sequence([
        Animated.timing(pulse,{toValue:1,duration:1200,useNativeDriver:true}),
        Animated.timing(pulse,{toValue:0,duration:1200,useNativeDriver:true}),
      ])
    );
    loop.start();
    return ()=>loop.stop();
  },[isDisabled,action.primary,pulse]);

  const animate=(toValue:number)=>Animated.timing(press,{toValue,duration:toValue?75:110,useNativeDriver:true}).start();
  const release=()=>Animated.spring(press,{toValue:0,speed:28,bounciness:6,useNativeDriver:true}).start();

  const borderRadius = shape === 'wide' ? Math.max(12, rect.height * 0.45) : Math.max(16, rect.width * 0.5);

  return <Animated.View
    pointerEvents='box-none'
    style={{
      position:'absolute',
      ...rect,
      transform:[
        {translateY:press.interpolate({inputRange:[0,1],outputRange:[0,Math.max(2,rect.height*.035)]})},
        {scale:isDisabled ? 1 : Animated.multiply(
          press.interpolate({inputRange:[0,1],outputRange:[1,.945]}),
          pulse.interpolate({inputRange:[0,1],outputRange:[1,action.primary?1.035:1]})
        )},
      ],
    }}
  >
    <Pressable
      accessibilityRole='button'
      accessibilityLabel={action.accessibilityLabel}
      accessibilityState={{disabled:isDisabled}}
      testID={action.testID}
      disabled={isDisabled}
      onPress={action.onPress}
      onPressIn={()=>{animate(1);emitDomeFeedback(action.feedback||'tap',{sound:action.sound!==false})}}
      onPressOut={release}
      style={[
        styles.hotspot,
        {borderRadius},
        !isDisabled && accent ? {
          shadowColor:accent,
          shadowOffset:{width:0,height:2},
          shadowOpacity:0.45,
          shadowRadius:7,
          elevation:4,
        } : null,
        !isDisabled&&action.primary?styles.primaryHotspot:null,
      ]}
    >
      {isDisabled ? (
        <View
          pointerEvents='none'
          style={[
            StyleSheet.absoluteFill,
            styles.disabledVeil,
            {borderRadius},
          ]}
        />
      ) : null}
    </Pressable>
  </Animated.View>;
}

function EnvironmentMotionLayer({image,scale}:{image:ShellRect;scale:number}){
  const balloonMotion=useRef(new Animated.Value(0)).current;
  const balloonTap=useRef(new Animated.Value(0)).current;
  const cloudFar=useRef(new Animated.Value(0)).current;
  const cloudTap=useRef(new Animated.Value(0)).current;
  const cloudNear=useRef(new Animated.Value(0)).current;
  const islandMotion=useRef(new Animated.Value(0)).current;
  const islandTap=useRef(new Animated.Value(0)).current;
  const leafMotion=useRef(new Animated.Value(0)).current;
  const leafTap=useRef(new Animated.Value(0)).current;
  const starGlow=useRef(new Animated.Value(0)).current;
  const starTap=useRef(new Animated.Value(0)).current;
  const sparkle=useRef(new Animated.Value(0)).current;
  const lightShift=useRef(new Animated.Value(0)).current;
  const[reduceMotion,setReduceMotion]=useState(false);

  useEffect(()=>{
    let mounted=true;
    void AccessibilityInfo.isReduceMotionEnabled().then(value=>{if(mounted)setReduceMotion(value)}).catch(()=>{});
    const subscription=AccessibilityInfo.addEventListener('reduceMotionChanged',setReduceMotion);
    return()=>{mounted=false;subscription.remove()};
  },[]);

  useEffect(()=>{
    balloonMotion.stopAnimation();cloudFar.stopAnimation();cloudNear.stopAnimation();islandMotion.stopAnimation();leafMotion.stopAnimation();starGlow.stopAnimation();sparkle.stopAnimation();lightShift.stopAnimation();
    balloonMotion.setValue(0);cloudFar.setValue(0);cloudNear.setValue(0);islandMotion.setValue(0);leafMotion.setValue(0);starGlow.setValue(0);sparkle.setValue(0);lightShift.setValue(0);
    if(reduceMotion)return;

    // Balloon: gentle floating swing
    const balloonAnim=Animated.loop(Animated.sequence([
      Animated.timing(balloonMotion,{toValue:1,duration:3800,useNativeDriver:true}),
      Animated.timing(balloonMotion,{toValue:0,duration:3800,useNativeDriver:true}),
    ]));

    // Dual-speed window clouds for parallax depth
    const cloudFarAnim=Animated.loop(Animated.sequence([
      Animated.timing(cloudFar,{toValue:1,duration:8200,useNativeDriver:true}),
      Animated.timing(cloudFar,{toValue:0,duration:8200,useNativeDriver:true}),
    ]));
    const cloudNearAnim=Animated.loop(Animated.sequence([
      Animated.timing(cloudNear,{toValue:1,duration:5400,useNativeDriver:true}),
      Animated.timing(cloudNear,{toValue:0,duration:5400,useNativeDriver:true}),
    ]));

    // Floating island in the window: gentle hover & slight horizontal drift
    const islandAnim=Animated.loop(Animated.sequence([
      Animated.timing(islandMotion,{toValue:1,duration:4600,useNativeDriver:true}),
      Animated.timing(islandMotion,{toValue:0,duration:4600,useNativeDriver:true}),
    ]));

    // Foliage/plants: organic soft sway with gentle rustling
    const leafAnim=Animated.loop(Animated.sequence([
      Animated.timing(leafMotion,{toValue:1,duration:3000,useNativeDriver:true}),
      Animated.timing(leafMotion,{toValue:0,duration:3000,useNativeDriver:true}),
    ]));

    // Star lamp on stack of books: warm soothing glow pulsation
    const starAnim=Animated.loop(Animated.sequence([
      Animated.timing(starGlow,{toValue:1,duration:2200,useNativeDriver:true}),
      Animated.timing(starGlow,{toValue:0,duration:2200,useNativeDriver:true}),
    ]));

    // Sparkles / magic dust motes
    const sparklesAnim=Animated.loop(Animated.sequence([
      Animated.timing(sparkle,{toValue:1,duration:3400,useNativeDriver:true}),
      Animated.timing(sparkle,{toValue:0,duration:3400,useNativeDriver:true}),
    ]));

    // Daylight shift
    const lightAnim=Animated.loop(Animated.sequence([
      Animated.timing(lightShift,{toValue:1,duration:6200,useNativeDriver:true}),
      Animated.timing(lightShift,{toValue:0,duration:6200,useNativeDriver:true}),
    ]));

    balloonAnim.start();cloudFarAnim.start();cloudNearAnim.start();islandAnim.start();leafAnim.start();starAnim.start();sparklesAnim.start();lightAnim.start();
    return()=>{balloonAnim.stop();cloudFarAnim.stop();cloudNearAnim.stop();islandAnim.stop();leafAnim.stop();starAnim.stop();sparklesAnim.stop();lightAnim.stop()};
  },[balloonMotion,cloudFar,cloudNear,islandMotion,leafMotion,starGlow,sparkle,lightShift,reduceMotion]);

  const reactToWorldTap=(detail:'star'|'balloon'|'cloud'|'leaves'|'island')=>{
    const motion={star:starTap,balloon:balloonTap,cloud:cloudTap,leaves:leafTap,island:islandTap}[detail];
    console.info('DOME_WORLD_DETAIL_TAP',{detail});
    emitDomeFeedback(detail==='star'?'success':detail==='island'?'primary':'tap');
    if(reduceMotion)return;
    motion.stopAnimation();motion.setValue(0);
    Animated.sequence([
      Animated.timing(motion,{toValue:1,duration:detail==='star'?115:150,useNativeDriver:true}),
      Animated.spring(motion,{toValue:0,speed:18,bounciness:detail==='leaves'?12:8,useNativeDriver:true}),
    ]).start();
  };
  const hotspot=(detail:'star'|'balloon'|'cloud'|'leaves'|'island',left:number,top:number,width:number,height:number)=><Pressable
    key={detail}
    testID={`dome-world-${detail}`}
    accessibilityRole='button'
    accessibilityLabel={{star:'Волшебная звезда',balloon:'Воздушный шар',cloud:'Облако',leaves:'Листья',island:'Летающий остров'}[detail]}
    onPress={()=>reactToWorldTap(detail)}
    hitSlop={8}
    style={{position:'absolute',left:image.left+left*scale,top:image.top+top*scale,width:width*scale,height:height*scale}}
  />;

  return <>
  {!reduceMotion?<View pointerEvents='none' testID='dome-environment-motion' style={StyleSheet.absoluteFill}>
    {/* Atmospheric soft light movement in the window sky */}
    <Animated.View style={[styles.skyGlow,{left:image.left+250*scale,top:image.top+90*scale,width:300*scale,height:290*scale,opacity:lightShift.interpolate({inputRange:[0,1],outputRange:[.08,.24]}),transform:[{scale:lightShift.interpolate({inputRange:[0,1],outputRange:[.96,1.06]})}]}]}/>

    {/* Floating Island outside window — gentle floating hover and drift */}
    <Animated.View testID='dome-island-motion' style={{position:'absolute',left:image.left+240*scale,top:image.top+155*scale,width:170*scale,height:140*scale,overflow:'hidden',transform:[{translateY:Animated.add(islandMotion.interpolate({inputRange:[0,1],outputRange:[-3.5*scale,3.5*scale]}),islandTap.interpolate({inputRange:[0,1],outputRange:[0,-12*scale]}))},{translateX:islandMotion.interpolate({inputRange:[0,1],outputRange:[-2*scale,2*scale]})},{scale:islandTap.interpolate({inputRange:[0,1],outputRange:[1,1.045]})}]}}><Image source={shellArtwork} style={{position:'absolute',left:-240*scale,top:-155*scale,width:SHELL_SOURCE.width*scale,height:SHELL_SOURCE.height*scale}}/></Animated.View>

    {/* Distant Cloud (far layer) — slow drift */}
    <Animated.View testID='dome-cloud-far-motion' style={{position:'absolute',left:image.left+265*scale,top:image.top+72*scale,width:160*scale,height:56*scale,overflow:'hidden',opacity:cloudFar.interpolate({inputRange:[0,0.5,1],outputRange:[0.65,0.92,0.65]}),transform:[{translateX:Animated.add(cloudFar.interpolate({inputRange:[0,1],outputRange:[-3*scale,8*scale]}),cloudTap.interpolate({inputRange:[0,1],outputRange:[0,16*scale]}))}]}}><Image source={shellArtwork} style={{position:'absolute',left:-265*scale,top:-72*scale,width:SHELL_SOURCE.width*scale,height:SHELL_SOURCE.height*scale}}/></Animated.View>

    {/* Near Cloud (mid layer) — slightly faster drift */}
    <Animated.View testID='dome-cloud-near-motion' style={{position:'absolute',left:image.left+365*scale,top:image.top+92*scale,width:155*scale,height:58*scale,overflow:'hidden',opacity:cloudNear.interpolate({inputRange:[0,0.5,1],outputRange:[0.75,0.98,0.75]}),transform:[{translateX:cloudNear.interpolate({inputRange:[0,1],outputRange:[-4*scale,11*scale]})},{translateY:cloudNear.interpolate({inputRange:[0,1],outputRange:[2*scale,-2*scale]})}]}}><Image source={shellArtwork} style={{position:'absolute',left:-365*scale,top:-92*scale,width:SHELL_SOURCE.width*scale,height:SHELL_SOURCE.height*scale}}/></Animated.View>

    {/* Balloon — gentle graceful float with subtle breeze rotation */}
    <Animated.View testID='dome-balloon-motion' style={{position:'absolute',left:image.left+522*scale,top:image.top+356*scale,width:94*scale,height:142*scale,overflow:'hidden',transform:[{translateX:balloonMotion.interpolate({inputRange:[0,1],outputRange:[-4*scale,6*scale]})},{translateY:Animated.add(balloonMotion.interpolate({inputRange:[0,1],outputRange:[4*scale,-8*scale]}),balloonTap.interpolate({inputRange:[0,1],outputRange:[0,-18*scale]}))},{rotate:balloonMotion.interpolate({inputRange:[0,0.5,1],outputRange:['-2.5deg','2.5deg','-2.5deg']})},{scale:balloonTap.interpolate({inputRange:[0,1],outputRange:[1,1.05]})}]}}><Image source={shellArtwork} style={{position:'absolute',left:-522*scale,top:-356*scale,width:SHELL_SOURCE.width*scale,height:SHELL_SOURCE.height*scale}}/></Animated.View>

    {/* Leaf / plant decorative element — organic soft sway with gentle rustling */}
    <Animated.View testID='dome-leaf-motion' style={{position:'absolute',left:image.left+615*scale,top:image.top+615*scale,width:70*scale,height:90*scale,overflow:'hidden',transform:[{translateY:leafMotion.interpolate({inputRange:[0,1],outputRange:[0,-4*scale]})},{rotate:leafMotion.interpolate({inputRange:[0,1],outputRange:['-1.5deg','3.5deg']})},{scale:Animated.multiply(leafMotion.interpolate({inputRange:[0,0.5,1],outputRange:[0.98,1.02,0.98]}),leafTap.interpolate({inputRange:[0,1],outputRange:[1,1.12]}))}]}}><Image source={shellArtwork} style={{position:'absolute',left:-615*scale,top:-615*scale,width:SHELL_SOURCE.width*scale,height:SHELL_SOURCE.height*scale}}/></Animated.View>

    {/* Star Lamp on stack of books — warm pulsing ambient glow */}
    <Animated.View style={[styles.starLampOuter,{left:image.left+722*scale,top:image.top+1024*scale,width:90*scale,height:90*scale,opacity:Animated.multiply(starGlow.interpolate({inputRange:[0,0.5,1],outputRange:[0.25,0.72,0.25]}),starTap.interpolate({inputRange:[0,1],outputRange:[1,1.3]})),transform:[{scale:Animated.multiply(starGlow.interpolate({inputRange:[0,0.5,1],outputRange:[0.94,1.12,0.94]}),starTap.interpolate({inputRange:[0,1],outputRange:[1,1.24]}))}]}]}/>
    <Animated.View style={[styles.starLampCore,{left:image.left+747*scale,top:image.top+1049*scale,width:40*scale,height:40*scale,opacity:Animated.multiply(starGlow.interpolate({inputRange:[0,0.5,1],outputRange:[0.45,0.95,0.45]}),starTap.interpolate({inputRange:[0,1],outputRange:[1,1.05]})),transform:[{scale:Animated.multiply(starGlow.interpolate({inputRange:[0,0.5,1],outputRange:[0.96,1.08,0.96]}),starTap.interpolate({inputRange:[0,1],outputRange:[1,1.18]}))}]}]}/>

    {/* Floating magic sparkles around the classroom */}
    <Animated.View style={[styles.catSparkle,{left:image.left+91*scale,top:image.top+1090*scale,width:14*scale,height:14*scale,opacity:balloonMotion.interpolate({inputRange:[0,1],outputRange:[.2,.8]}),transform:[{translateY:balloonMotion.interpolate({inputRange:[0,1],outputRange:[3*scale,-3*scale]})},{rotate:balloonMotion.interpolate({inputRange:[0,1],outputRange:['0deg','25deg']})}]}]}/>
    <Animated.View style={[styles.catSparkle,{left:image.left+220*scale,top:image.top+240*scale,width:11*scale,height:11*scale,opacity:sparkle.interpolate({inputRange:[0,0.5,1],outputRange:[.1,.85,.1]}),transform:[{translateY:sparkle.interpolate({inputRange:[0,1],outputRange:[0,-12*scale]})},{rotate:sparkle.interpolate({inputRange:[0,1],outputRange:['0deg','40deg']})}]}]}/>
    <Animated.View style={[styles.catSparkle,{left:image.left+680*scale,top:image.top+460*scale,width:13*scale,height:13*scale,opacity:sparkle.interpolate({inputRange:[0,0.6,1],outputRange:[.55,.1,.55]}),transform:[{translateY:sparkle.interpolate({inputRange:[0,1],outputRange:[-6*scale,5*scale]})},{scale:sparkle.interpolate({inputRange:[0,0.5,1],outputRange:[0.85,1.2,0.85]})}]}]}/>
    <Animated.View style={[styles.catSparkle,{left:image.left+135*scale,top:image.top+640*scale,width:10*scale,height:10*scale,opacity:cloudFar.interpolate({inputRange:[0,0.5,1],outputRange:[.2,.7,.2]}),transform:[{translateY:cloudFar.interpolate({inputRange:[0,1],outputRange:[4*scale,-5*scale]})},{rotate:cloudFar.interpolate({inputRange:[0,1],outputRange:['10deg','-20deg']})}]}]}/>
  </View>:null}
  <View testID='dome-environment-interactions' pointerEvents='box-none' style={styles.environmentInteractions}>
    {hotspot('cloud',250,55,290,115)}
    {hotspot('island',225,145,200,160)}
    {hotspot('balloon',505,338,125,160)}
    {hotspot('leaves',735,585,95,150)}
    {hotspot('star',718,1005,112,125)}
  </View>
  </>;
}


export function LessonPortraitShell({visual,prompt,progressLabel,replay,answer,hint,more,continueAction,overlay,recordingTools,mascotState}:Props){
  const[size,setSize]=useState({width:360,height:720});const layout=useMemo(()=>lessonShellLayout(size.width,size.height),[size.width,size.height]);
  return <View testID='dome-lesson-portrait-shell' onLayout={event=>{const{width,height}=event.nativeEvent.layout;if(width>0&&height>0&&(Math.abs(width-size.width)>.5||Math.abs(height-size.height)>.5))setSize({width,height})}} style={styles.root}>
    <Image testID='dome-lesson-shell-artwork' source={shellArtwork} resizeMode='contain' style={{position:'absolute',...layout.image}}/>
    <EnvironmentMotionLayer image={layout.image} scale={layout.scale}/>
    <View testID='dome-lesson-central-panel' style={[styles.panel,layout.content]}>
      <View style={styles.progressPill}><Text style={styles.progressText}>{progressLabel}</Text></View>
      <View style={styles.visual}>{visual}</View>
      <View style={[styles.prompt,recordingTools?styles.promptWithRecordingTools:null]}>{prompt}</View>
      {recordingTools?<View testID='portrait-saved-recording-tools' style={styles.recordingTools}>{recordingTools}</View>:null}
      {overlay?<View style={styles.overlay}>{overlay}</View>:null}
    </View>

    {/* Dynamic DOME Mascot sitting in classroom */}
    <View
      pointerEvents='box-none'
      style={{
        position:'absolute',
        left:layout.image.left+2*layout.scale,
        top:layout.image.top+1040*layout.scale,
        zIndex:25,
        elevation:25,
      }}
    >
      <DomeMascot
        state={mascotState||'HELLO'}
        size={280*layout.scale}
        testID='dome-portrait-mascot'
        onPress={()=>{console.info('DOME_WORLD_DETAIL_TAP',{detail:'cat'});emitDomeFeedback('tap')}}
      />
    </View>
    <PhysicalHotspot rect={layout.controls.replay} action={replay} accent='#2d83ca'/>
    {answer?<PhysicalHotspot rect={layout.controls.answer} action={answer} accent='#66b820'/>:null}
    {hint?<PhysicalHotspot rect={layout.controls.hint} action={hint} accent='#efad21'/>:null}
    <PhysicalHotspot rect={layout.controls.more} action={more} accent='#8d54c5'/>
    <PhysicalHotspot rect={layout.controls.continue} action={continueAction} shape='wide' accent='#43a047'/>
  </View>;
}

const styles=StyleSheet.create({
  root:{flex:1,overflow:'hidden',backgroundColor:'#bce8ff'},
  panel:{position:'absolute',overflow:'hidden',borderRadius:26,padding:7},
  progressPill:{position:'absolute',right:9,top:7,zIndex:20,borderRadius:12,backgroundColor:'rgba(255,255,255,.88)',paddingHorizontal:8,paddingVertical:3},
  progressText:{fontSize:11,fontWeight:'800',color:'#52606d'},
  visual:{flex:1,minHeight:0,paddingTop:2},
  prompt:{alignSelf:'flex-end',width:'78%',maxHeight:'27%',marginBottom:'4%',borderRadius:13,backgroundColor:'rgba(255,250,238,.94)',paddingHorizontal:9,paddingVertical:5,borderWidth:1,borderColor:'rgba(224,174,66,.28)'},
  promptWithRecordingTools:{maxHeight:'19%',marginBottom:60},
  recordingTools:{position:'absolute',left:9,right:9,bottom:7,zIndex:26},
  overlay:{position:'absolute',left:8,right:8,bottom:8,zIndex:30,borderRadius:14,backgroundColor:'rgba(255,255,255,.97)',padding:8},
  environmentInteractions:{position:'absolute',left:0,right:0,top:0,bottom:0,zIndex:12},
  hotspot:{flex:1,backgroundColor:'transparent'},
  primaryHotspot:{borderWidth:2,borderColor:'rgba(255,255,255,.78)'},
  disabledVeil:{backgroundColor:'rgba(226, 232, 240, 0.76)',borderWidth:2,borderColor:'rgba(100, 116, 139, 0.58)'},
  skyGlow:{position:'absolute',borderRadius:999,backgroundColor:'rgba(255,235,135,.72)'},
  starLampOuter:{position:'absolute',borderRadius:999,backgroundColor:'rgba(255,180,60,.42)'},
  starLampCore:{position:'absolute',borderRadius:999,backgroundColor:'rgba(255,248,160,.82)'},
  catSparkle:{position:'absolute',borderRadius:4,backgroundColor:'rgba(255,248,181,.92)',shadowColor:'#fff',shadowOpacity:.8,shadowRadius:5,elevation:2},
  catBlink:{position:'absolute',zIndex:6,borderRadius:999,backgroundColor:'rgba(45,31,22,.78)'},
});
