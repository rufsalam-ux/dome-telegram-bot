import React,{useCallback,useMemo,useRef,useState} from 'react';
import {Animated,Image,PanResponder,Pressable,Text,useWindowDimensions,View} from 'react-native';
import {movedPixelRect,suitcaseDropAccepted,suitcaseDropOutcome,suitcaseTapFallbackAvailable,updatePackedItems,validPixelRect,type PixelPoint,type PixelRect} from '../engine/lessonRuntime';
import {playExperience} from '../experience/experience';

export type SuitcaseItem={id:string;label:string;useful:boolean;image:any};

type NativeView=React.ElementRef<typeof View>;

function measure(ref:React.RefObject<any>):Promise<PixelRect|undefined>{
  return new Promise(resolve=>{
    let settled=false;const finish=(value?:PixelRect)=>{if(settled)return;settled=true;clearTimeout(timer);resolve(value)};
    const timer=setTimeout(()=>finish(undefined),180);const node=ref.current;
    if(!node?.measureInWindow){finish(undefined);return}
    node.measureInWindow((x:number,y:number,width:number,height:number)=>{const value={x,y,width,height};finish(validPixelRect(value)?value:undefined)});
  });
}

function pagePoint(event:any,gesture:any):PixelPoint|undefined{
  const native=event?.nativeEvent||{};const touch=native.changedTouches?.[0]||native.touches?.[0]||native;
  const x=Number.isFinite(touch?.pageX)?Number(touch.pageX):Number(gesture?.moveX);
  const y=Number.isFinite(touch?.pageY)?Number(touch.pageY):Number(gesture?.moveY);
  return Number.isFinite(x)&&Number.isFinite(y)?{x,y}:undefined;
}

function Draggable({item,targetRef,packed,onCommit,onDragging,onHover,onFailedDrop,size}:{item:SuitcaseItem;targetRef:React.RefObject<NativeView|null>;packed:boolean;onCommit:(id:string,inside:boolean)=>void|Promise<void>;onDragging:(value:boolean)=>void;onHover:(inside:boolean)=>void;onFailedDrop:(id:string)=>void;size:number}){
  const position=useRef(new Animated.ValueXY()).current;const itemRef=useRef<any>(null);const targetRectRef=useRef<PixelRect|undefined>(undefined);const itemRectRef=useRef<PixelRect|undefined>(undefined);
  const handlersRef=useRef({packed,onCommit,onDragging,onHover,onFailedDrop});handlersRef.current={packed,onCommit,onDragging,onHover,onFailedDrop};
  function refreshRects(){void measure(targetRef).then(value=>{if(value)targetRectRef.current=value});void measure(itemRef).then(value=>{if(value)itemRectRef.current=value})}
  function springBack(){Animated.spring(position,{toValue:{x:0,y:0},useNativeDriver:true,speed:22,bounciness:7}).start()}
  const responder=useMemo(()=>PanResponder.create({
    onStartShouldSetPanResponder:()=>true,
    onMoveShouldSetPanResponder:(_event,gesture)=>Math.abs(gesture.dx)+Math.abs(gesture.dy)>2,
    onPanResponderGrant:()=>{position.stopAnimation();position.setValue({x:0,y:0});handlersRef.current.onDragging(true);playExperience('DRAG_PICKUP');refreshRects()},
    onPanResponderMove:(event,gesture)=>{position.setValue({x:gesture.dx,y:gesture.dy});handlersRef.current.onHover(suitcaseDropAccepted(pagePoint(event,gesture),movedPixelRect(itemRectRef.current,gesture.dx,gesture.dy),targetRectRef.current))},
    onPanResponderRelease:(event,gesture)=>{const point=pagePoint(event,gesture);const moved=movedPixelRect(itemRectRef.current,gesture.dx,gesture.dy);void (async()=>{
      // Keep the parent ScrollView frozen until the native target rectangle is
      // captured. Re-enabling it first changes Android window coordinates.
      const target=targetRectRef.current||await measure(targetRef);const inside=suitcaseDropAccepted(point,moved,target);
      const handlers=handlersRef.current;handlers.onHover(false);handlers.onDragging(false);const outcome=suitcaseDropOutcome(handlers.packed,inside);
      if(outcome!=='RETURN'){
        try{playExperience('DROP_CORRECT');await handlers.onCommit(item.id,inside)}catch{playExperience('TRY_AGAIN');handlers.onFailedDrop(item.id)}
      }else if(!handlers.packed){playExperience('TRY_AGAIN');handlers.onFailedDrop(item.id)}
      springBack();
    })()},
    onPanResponderTerminate:()=>{handlersRef.current.onHover(false);handlersRef.current.onDragging(false);springBack()},
    onShouldBlockNativeResponder:()=>true,
  }),[item.id,position,targetRef]);
  return <Animated.View ref={itemRef} collapsable={false} testID={`suitcase-drag-${packed?'packed':'available'}-${item.id}`} accessibilityRole='adjustable' accessibilityLabel={item.label} {...responder.panHandlers} style={{width:size,height:size,alignItems:'center',justifyContent:'center',zIndex:packed?8:5,elevation:packed?3:1,transform:position.getTranslateTransform()}}>
    <Image source={item.image} style={{width:size-4,height:size-4,resizeMode:'contain'}}/>
  </Animated.View>;
}

export function DragDropSuitcase({items,packed,onChange,onDragging,disabled=false}:{items:SuitcaseItem[];packed:string[];onChange:(next:string[])=>void|Promise<void>;onDragging:(value:boolean)=>void;disabled?:boolean}){
  const targetRef=useRef<NativeView>(null);const {width}=useWindowDimensions();const itemSize=width<390?48:58;
  const[hover,setHover]=useState(false);const[failedDrags,setFailedDrags]=useState<Record<string,number>>({});
  const[committing,setCommitting]=useState(false);const committingRef=useRef(false);
  const available=items.filter(item=>!packed.includes(item.id));const selected=items.filter(item=>packed.includes(item.id));
  const commit=useCallback(async(id:string,inside:boolean)=>{
    if(disabled||committingRef.current)return;const outcome=suitcaseDropOutcome(packed.includes(id),inside);const next=updatePackedItems(packed,id,outcome);
    if(next===packed)return;committingRef.current=true;setCommitting(true);try{await onChange(next);setFailedDrags(current=>({...current,[id]:0}))}finally{committingRef.current=false;setCommitting(false)}
  },[disabled,onChange,packed]);
  const failed=useCallback((id:string)=>{setFailedDrags(current=>({...current,[id]:(current[id]||0)+1}))},[]);
  async function accessiblePack(item:SuitcaseItem){if(disabled||!suitcaseTapFallbackAvailable(failedDrags[item.id]||0))return;playExperience('DROP_CORRECT');await commit(item.id,true)}
  return <View pointerEvents={disabled||committing?'none':'auto'} style={{gap:6}}>
    <View ref={targetRef} collapsable={false} testID='suitcase-drop-zone' accessibilityLabel='Чемодан — зона для предметов' style={{height:width<390?116:138,borderWidth:hover?5:3,borderStyle:'dashed',borderColor:hover?'#13a864':'#6aa6d8',borderRadius:18,overflow:'hidden',alignItems:'center',justifyContent:'center',backgroundColor:hover?'#dff8e9':'#eef8ff'}}>
      <Image source={require('../../assets/lesson/demo_001/suitcase-authored/suitcase-target.png')} style={{position:'absolute',width:'96%',height:'96%',resizeMode:'contain'}}/>
      <View pointerEvents='box-none' style={{position:'absolute',left:'12%',right:'12%',bottom:6,flexDirection:'row',flexWrap:'wrap',justifyContent:'center'}}>
        {selected.map(item=><Draggable key={`packed-${item.id}`} item={item} targetRef={targetRef} packed onCommit={commit} onDragging={onDragging} onHover={setHover} onFailedDrop={failed} size={Math.max(38,itemSize-10)}/>) }
      </View>
      {hover?<Text pointerEvents='none' style={{position:'absolute',top:4,right:10,fontWeight:'800',color:'#087a43'}}>Отпусти здесь ✓</Text>:null}
    </View>
    <Text style={{fontSize:13,color:'#526072',textAlign:'center'}}>Перетащи предмет в чемодан. Его можно вынуть обратно.</Text>
    <View style={{minHeight:itemSize*2+4,flexDirection:'row',flexWrap:'wrap',justifyContent:'space-around',alignContent:'center'}}>
      {available.map(item=><View key={item.id} style={{alignItems:'center'}}><Draggable item={item} targetRef={targetRef} packed={false} onCommit={commit} onDragging={onDragging} onHover={setHover} onFailedDrop={failed} size={itemSize}/>{suitcaseTapFallbackAvailable(failedDrags[item.id]||0)?<Pressable testID={`suitcase-tap-fallback-${item.id}`} accessibilityRole='button' accessibilityLabel={`Положить ${item.label} в чемодан`} onPress={()=>void accessiblePack(item)} style={{paddingHorizontal:6,paddingVertical:3,borderRadius:8,backgroundColor:'#e8f6ee'}}><Text style={{fontSize:11,color:'#087a43',fontWeight:'700'}}>Положить</Text></Pressable>:null}</View>) }
    </View>
  </View>;
}
