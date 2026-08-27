import React,{useMemo,useRef,useState} from 'react';
import {Alert,Image,PanResponder,ScrollView,Text,useWindowDimensions,View} from 'react-native';

import {API_BASE,confirmHeroGeometry} from '../api/mobile';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {useAppStore} from '../store/AppStore';
import type {HeroMetadata,HeroPoint} from '../types/domain';

const clamp=(value:number)=>Math.max(0,Math.min(1,value));
const point=(value:unknown,fallback:HeroPoint):HeroPoint=>Array.isArray(value)&&value.length===2?[clamp(Number(value[0])||0),clamp(Number(value[1])||0)]:fallback;

function Marker({label,color,value,width,height,onChange}:{label:string;color:string;value:HeroPoint;width:number;height:number;onChange:(next:HeroPoint)=>void}){
  const start=useRef<HeroPoint>(value);start.current=value;
  const pan=useMemo(()=>PanResponder.create({
    onStartShouldSetPanResponder:()=>true,
    onMoveShouldSetPanResponder:()=>true,
    onPanResponderGrant:()=>{start.current=value},
    onPanResponderMove:(_,gesture)=>onChange([clamp(start.current[0]+gesture.dx/Math.max(1,width)),clamp(start.current[1]+gesture.dy/Math.max(1,height))]),
  }),[height,width,onChange,value]);
  return <View {...pan.panHandlers} accessibilityRole='adjustable' accessibilityLabel={label} style={{position:'absolute',left:`${value[0]*100}%`,top:`${value[1]*100}%`,transform:[{translateX:-17},{translateY:-17}],alignItems:'center',zIndex:8}}>
    <View style={{width:34,height:34,borderRadius:17,borderWidth:3,borderColor:'#fff',backgroundColor:color,alignItems:'center',justifyContent:'center'}}><Text style={{color:'#fff',fontWeight:'900'}}>＋</Text></View>
    <Text style={{marginTop:2,paddingHorizontal:6,paddingVertical:2,borderRadius:8,overflow:'hidden',backgroundColor:'rgba(0,0,0,.72)',color:'#fff',fontSize:11,fontWeight:'800'}}>{label}</Text>
  </View>;
}

export function HeroConfirmScreen(){
  const store=useAppStore();const child=store.selectedChild;const dimensions=useWindowDimensions();const metadata=child?.heroMetadata;
  const bbox=metadata?.characterBoundingBox||[0,0,1,1];const facingInitial=metadata?.canonicalFacing||metadata?.facingDirection||'FRONT';
  const[head,setHead]=useState<HeroPoint>(()=>point(metadata?.headPoint,[metadata?.headCenterX??.5,metadata?.headCenterY??.25]));
  const[front,setFront]=useState<HeroPoint>(()=>point(metadata?.frontPoint,[facingInitial==='LEFT'?bbox[0]:facingInitial==='RIGHT'?bbox[0]+bbox[2]:.5,head[1]]));
  const[back,setBack]=useState<HeroPoint>(()=>point(metadata?.backPoint,[facingInitial==='LEFT'?bbox[0]+bbox[2]:facingInitial==='RIGHT'?bbox[0]:.5,metadata?.bodyCenterY??.62]));
  const[leftArm,setLeftArm]=useState<HeroPoint>(()=>point(metadata?.leftArmOrFrontLimb,[bbox[0]+bbox[2]*.32,metadata?.bodyCenterY??.6]));
  const[rightArm,setRightArm]=useState<HeroPoint>(()=>point(metadata?.rightArmOrFrontLimb,[bbox[0]+bbox[2]*.68,metadata?.bodyCenterY??.6]));
  const[feet,setFeet]=useState<HeroPoint>(()=>point(metadata?.feetAnchor||metadata?.groundAnchor,[.5,bbox[1]+bbox[3]]));
  const[tail,setTail]=useState<HeroPoint>(()=>point(metadata?.tailPoint,back));const[facing,setFacing]=useState<'LEFT'|'RIGHT'|'FRONT'>(facingInitial==='LEFT'||facingInitial==='RIGHT'?facingInitial:'FRONT');const[busy,setBusy]=useState(false);
  if(!child||!metadata||!child.activeCharacterId||!child.heroUrl)return <View style={{padding:24}}><H1>Герой не выбран</H1><Button title='Вернуться к выбору' onPress={()=>store.setScreen('hero')}/></View>;
  const sourceAspect=Math.max(.25,Math.min(4,Number(metadata.sourceWidth||1)/Math.max(1,Number(metadata.sourceHeight||1))));const availableWidth=Math.max(160,dimensions.width-32);let frameHeight=Math.min(440,availableWidth/sourceAspect);let frameWidth=frameHeight*sourceAspect;if(frameWidth>availableWidth){frameWidth=availableWidth;frameHeight=frameWidth/sourceAspect}const uri=child.heroUrl.startsWith('http')?child.heroUrl:API_BASE+child.heroUrl;const hasTail=Boolean(metadata.tailPoint||metadata.tailBoundingBox);
  const confirm=async()=>{try{setBusy(true);const response=await confirmHeroGeometry(child.id,child.activeCharacterId!,{headPoint:head,frontPoint:front,backPoint:back,leftArmOrFrontLimb:leftArm,rightArmOrFrontLimb:rightArm,feetAnchor:feet,groundAnchor:feet,tailPoint:hasTail?tail:null,facingDirection:facing,canonicalFacing:facing});store.updateChild({...child,heroMetadata:response.hero_metadata});store.setScreen('home')}catch(error:any){Alert.alert('Не удалось сохранить разметку',error.message)}finally{setBusy(false)}};
  return <ScrollView contentContainerStyle={{padding:16,paddingBottom:32}}><H1>Проверим героя один раз</H1><Body>Перетащите метки, если DOME ошибся. Эта разметка сохранится и будет одинаково использоваться в уроках и мультфильмах.</Body>
    <View testID='hero-anatomy-canvas' style={{alignSelf:'center',width:frameWidth,height:frameHeight,marginVertical:12,borderRadius:18,overflow:'hidden',backgroundColor:'#eef6ff',borderWidth:2,borderColor:'#b9d8ff'}}>
      <Image source={{uri}} resizeMode='stretch' style={{width:'100%',height:'100%'}}/>
      <Marker label='ГОЛОВА' color='#e5484d' value={head} width={frameWidth} height={frameHeight} onChange={setHead}/>
      <Marker label='ПЕРЕД' color='#1a73e8' value={front} width={frameWidth} height={frameHeight} onChange={setFront}/>
      <Marker label='ЗАД' color='#7b61a8' value={back} width={frameWidth} height={frameHeight} onChange={setBack}/>
      <Marker label='ЛЕВАЯ ЛАПА' color='#d34fa5' value={leftArm} width={frameWidth} height={frameHeight} onChange={setLeftArm}/>
      <Marker label='ПРАВАЯ ЛАПА' color='#00a0a0' value={rightArm} width={frameWidth} height={frameHeight} onChange={setRightArm}/>
      <Marker label='НОГИ / ОПОРА' color='#138a55' value={feet} width={frameWidth} height={frameHeight} onChange={setFeet}/>
      {hasTail?<Marker label='ХВОСТ' color='#e07a16' value={tail} width={frameWidth} height={frameHeight} onChange={setTail}/>:null}
    </View>
    <Card><H2>Куда смотрит герой?</H2><View style={{gap:7}}><Button secondary={facing!=='LEFT'} title={`${facing==='LEFT'?'✓ ':''}Смотрит влево`} onPress={()=>setFacing('LEFT')}/><Button secondary={facing!=='RIGHT'} title={`${facing==='RIGHT'?'✓ ':''}Смотрит вправо`} onPress={()=>setFacing('RIGHT')}/><Button secondary={facing!=='FRONT'} title={`${facing==='FRONT'?'✓ ':''}Смотрит прямо`} onPress={()=>setFacing('FRONT')}/></View></Card>
    <Button testID='confirm-hero-geometry' disabled={busy} title={busy?'Сохраняю…':'Подтвердить героя'} onPress={confirm}/><Button secondary disabled={busy} title='Назад к выбору' onPress={()=>store.setScreen('hero')}/>
  </ScrollView>;
}
