import React,{useMemo,useRef} from 'react';
import {Animated,Image,PanResponder,Text,useWindowDimensions,Vibration,View} from 'react-native';
import {useAudioPlayer} from 'expo-audio';
import {dropInsideTarget,type PixelRect} from '../engine/lessonRuntime';

const popSound=require('../../assets/sounds/suitcase-pop.wav');
const returnSound=require('../../assets/sounds/suitcase-return.wav');

export type SuitcaseItem={id:string;label:string;useful:boolean;image:any};

type NativeView=React.ElementRef<typeof View>;

function measure(ref:React.RefObject<NativeView|null>):Promise<PixelRect>{
  return new Promise(resolve=>{
    if(!ref.current){resolve({x:0,y:0,width:0,height:0});return}
    ref.current.measureInWindow((x,y,width,height)=>resolve({x,y,width,height}));
  });
}

function play(player:any){try{player.seekTo(0);player.play()}catch{}}

function Draggable({item,targetRef,packed,onCommit,onDragging,popPlayer,returnPlayer,size}:{item:SuitcaseItem;targetRef:React.RefObject<NativeView|null>;packed:boolean;onCommit:(id:string,inside:boolean)=>void|Promise<void>;onDragging:(value:boolean)=>void;popPlayer:any;returnPlayer:any;size:number}){
  const position=useRef(new Animated.ValueXY()).current;
  const responder=useMemo(()=>PanResponder.create({
    onStartShouldSetPanResponder:()=>true,
    onMoveShouldSetPanResponder:(_event,gesture)=>Math.abs(gesture.dx)+Math.abs(gesture.dy)>2,
    onPanResponderGrant:()=>{onDragging(true);position.setOffset({x:(position.x as any)._value,y:(position.y as any)._value});position.setValue({x:0,y:0})},
    onPanResponderMove:Animated.event([null,{dx:position.x,dy:position.y}],{useNativeDriver:false}),
    onPanResponderRelease:(_event,gesture)=>{position.flattenOffset();void (async()=>{
      const target=await measure(targetRef);const inside=dropInsideTarget(gesture.moveX,gesture.moveY,target,4);
      const changed=packed?!inside:inside;
      if(changed){play(popPlayer);Vibration.vibrate(12);await onCommit(item.id,inside)}
      else{play(returnPlayer);Vibration.vibrate([0,8,24,8])}
      Animated.spring(position,{toValue:{x:0,y:0},useNativeDriver:true,speed:22,bounciness:7}).start();onDragging(false)
    })()},
    onPanResponderTerminate:()=>{Animated.spring(position,{toValue:{x:0,y:0},useNativeDriver:true}).start();onDragging(false)},
    onShouldBlockNativeResponder:()=>true,
  }),[item.id,packed,onCommit,onDragging,popPlayer,position,returnPlayer,targetRef]);
  return <Animated.View testID={`suitcase-drag-${packed?'packed':'available'}-${item.id}`} accessibilityRole='adjustable' accessibilityLabel={item.label} {...responder.panHandlers} style={{width:size,height:size,alignItems:'center',justifyContent:'center',zIndex:5,transform:position.getTranslateTransform()}}>
    <Image source={item.image} style={{width:size-4,height:size-4,resizeMode:'contain'}}/>
  </Animated.View>;
}

export function DragDropSuitcase({items,packed,onChange,onDragging,disabled=false}:{items:SuitcaseItem[];packed:string[];onChange:(next:string[])=>void|Promise<void>;onDragging:(value:boolean)=>void;disabled?:boolean}){
  const targetRef=useRef<NativeView>(null);const {width}=useWindowDimensions();const itemSize=width<390?48:58;
  const popPlayer=useAudioPlayer(popSound);const returnPlayer=useAudioPlayer(returnSound);
  const available=items.filter(item=>!packed.includes(item.id));const selected=items.filter(item=>packed.includes(item.id));
  async function commit(id:string,inside:boolean){
    if(disabled)return;
    const next=inside?Array.from(new Set([...packed,id])):packed.filter(value=>value!==id);await onChange(next);
  }
  return <View pointerEvents={disabled?'none':'auto'} style={{gap:6}}>
    <View ref={targetRef} testID='suitcase-drop-zone' style={{height:width<390?116:138,borderWidth:3,borderStyle:'dashed',borderColor:'#6aa6d8',borderRadius:18,overflow:'hidden',alignItems:'center',justifyContent:'center',backgroundColor:'#eef8ff'}}>
      <Image source={require('../../assets/lesson/demo_001/suitcase-authored/suitcase-target.png')} style={{position:'absolute',width:'96%',height:'96%',resizeMode:'contain'}}/>
      <View pointerEvents='box-none' style={{position:'absolute',left:'12%',right:'12%',bottom:6,flexDirection:'row',flexWrap:'wrap',justifyContent:'center'}}>
        {selected.map(item=><Draggable key={`packed-${item.id}`} item={item} targetRef={targetRef} packed onCommit={commit} onDragging={onDragging} popPlayer={popPlayer} returnPlayer={returnPlayer} size={Math.max(38,itemSize-10)}/>) }
      </View>
    </View>
    <Text style={{fontSize:13,color:'#526072',textAlign:'center'}}>Перетащи предмет в чемодан. Его можно вынуть обратно.</Text>
    <View style={{minHeight:itemSize*2+4,flexDirection:'row',flexWrap:'wrap',justifyContent:'space-around',alignContent:'center'}}>
      {available.map(item=><Draggable key={item.id} item={item} targetRef={targetRef} packed={false} onCommit={commit} onDragging={onDragging} popPlayer={popPlayer} returnPlayer={returnPlayer} size={itemSize}/>) }
    </View>
  </View>;
}
