import React,{useEffect,useMemo,useRef,useState} from 'react';
import {Animated,PanResponder,Text,View} from 'react-native';
import {DomePressable} from './DomePressable';
import {emitDomeFeedback,emitDragTextureFeedback} from '../experience/useDomeFeedback';

import {
  canonicalTaskType,expectedTargetId,initialPuzzleOrder,isStableTaskTemplate,
  memoryDeck,moveSequenceItem,puzzleSolved,sequenceSolved,swapPuzzlePieces,
  taskItems,taskPairs,taskTargets,type TaskOption,
} from '../engine/taskTemplateRuntime';

export type TemplateTaskResult={completed:boolean;correct:boolean;[key:string]:unknown};
type Box={x:number;y:number;width:number;height:number};

function inside(x:number,y:number,box?:Box):boolean{return Boolean(box&&x>=box.x&&x<=box.x+box.width&&y>=box.y&&y<=box.y+box.height)}
function measure(ref:React.RefObject<any>):Promise<Box|undefined>{return new Promise(resolve=>{const node=ref.current;if(!node?.measureInWindow)return resolve(undefined);node.measureInWindow((x:number,y:number,width:number,height:number)=>resolve(width>0&&height>0?{x,y,width,height}:undefined))})}

function Chip({value,active=false,done=false,onPress,testID}:{value:TaskOption;active?:boolean;done?:boolean;onPress?:()=>void;testID:string}){
  return <DomePressable testID={testID} accessibilityRole='button' disabled={done} onPress={onPress} style={{minHeight:50,minWidth:92,flex:1,borderRadius:14,borderWidth:3,borderColor:done?'#13a864':active?'#246bfd':'#d3dae6',backgroundColor:done?'#dff8e9':active?'#eef4ff':'#fff'}} contentStyle={{padding:8}}><Text style={{fontSize:16,fontWeight:'800',textAlign:'center',color:'#20243a'}}>{done?'✓ ':''}{value.label}</Text></DomePressable>;
}

function DragChip({item,targetRefs,onDrop,onDragging,disabled}:{item:TaskOption;targetRefs:React.RefObject<any>[];onDrop:(targetIndex:number)=>void;onDragging:(value:boolean)=>void;disabled:boolean}){
  const position=useRef(new Animated.ValueXY()).current;
  const handlers=useRef({onDrop,onDragging,disabled});handlers.current={onDrop,onDragging,disabled};
  const responder=useMemo(()=>PanResponder.create({
    onStartShouldSetPanResponder:()=>!handlers.current.disabled,
    onMoveShouldSetPanResponder:(_event,gesture)=>!handlers.current.disabled&&Math.abs(gesture.dx)+Math.abs(gesture.dy)>3,
    onPanResponderGrant:()=>{handlers.current.onDragging(true);position.setValue({x:0,y:0});emitDomeFeedback('dragStart')},
    onPanResponderMove:(_event,gesture)=>{position.setValue({x:gesture.dx,y:gesture.dy});emitDragTextureFeedback()},
    onPanResponderRelease:(event)=>{void Promise.all(targetRefs.map(measure)).then(boxes=>{const x=Number(event.nativeEvent.pageX);const y=Number(event.nativeEvent.pageY);handlers.current.onDrop(boxes.findIndex(box=>inside(x,y,box)));handlers.current.onDragging(false);Animated.spring(position,{toValue:{x:0,y:0},useNativeDriver:true}).start()})},
    onPanResponderTerminate:()=>{handlers.current.onDragging(false);Animated.spring(position,{toValue:{x:0,y:0},useNativeDriver:true}).start()},
    onShouldBlockNativeResponder:()=>true,
  }),[position,targetRefs]);
  return <Animated.View testID={`template-drag-${item.id}`} accessibilityRole='adjustable' {...responder.panHandlers} style={{minHeight:52,minWidth:96,borderRadius:14,backgroundColor:'#eef4ff',borderWidth:2,borderColor:'#246bfd',padding:9,alignItems:'center',justifyContent:'center',zIndex:10,elevation:4,transform:position.getTranslateTransform()}}><Text style={{fontWeight:'800',textAlign:'center'}}>{item.label}</Text></Animated.View>;
}

function DragDropTask({slide,initial,onResult,onDragging,disabled}:{slide:any;initial:any;onResult:(value:TemplateTaskResult)=>void;onDragging:(value:boolean)=>void;disabled:boolean}){
  const items=useMemo(()=>taskItems(slide),[slide]);const targets=useMemo(()=>taskTargets(slide),[slide]);
  const[assignments,setAssignments]=useState<Record<string,string>>(()=>initial?.assignments||{});
  const targetRefs=useMemo(()=>targets.map(()=>React.createRef<View>()),[targets.length,slide?.slide_id]);
  function drop(item:TaskOption,itemIndex:number,targetIndex:number){if(targetIndex<0){emitDomeFeedback('invalidDrop');return}const target=targets[targetIndex];if(!target)return;const expected=expectedTargetId(slide,item.id,itemIndex);if(target.id!==expected){emitDomeFeedback('invalidDrop');return}const next={...assignments,[item.id]:target.id};setAssignments(next);emitDomeFeedback('drop');const completed=items.every((value,index)=>next[value.id]===expectedTargetId(slide,value.id,index));void onResult({completed,correct:completed,assignments:next})}
  return <View testID='template-drag-drop' style={{gap:10}}><Text style={{fontWeight:'800'}}>Перетащи каждый объект на его место</Text><View style={{flexDirection:'row',flexWrap:'wrap',gap:8}}>{targets.map((target,index)=><View key={target.id} ref={targetRefs[index]} collapsable={false} testID={`template-drop-${target.id}`} style={{minHeight:70,minWidth:'45%',flex:1,borderRadius:14,borderWidth:3,borderStyle:'dashed',borderColor:'#6aa6d8',alignItems:'center',justifyContent:'center',padding:8,backgroundColor:'#eef8ff'}}><Text style={{fontWeight:'700',textAlign:'center'}}>{target.label}</Text><Text style={{color:'#087a43'}}>{Object.values(assignments).includes(target.id)?'✓':''}</Text></View>)}</View><View style={{flexDirection:'row',flexWrap:'wrap',gap:8}}>{items.map((item,index)=>assignments[item.id]?null:<DragChip key={item.id} item={item} targetRefs={targetRefs} disabled={disabled} onDragging={onDragging} onDrop={targetIndex=>drop(item,index,targetIndex)}/>)}</View></View>;
}

function MatchingTask({slide,initial,onResult,disabled}:{slide:any;initial:any;onResult:(value:TemplateTaskResult)=>void;disabled:boolean}){
  const pairs=useMemo(()=>taskPairs(slide),[slide]);const[selected,setSelected]=useState('');const[matched,setMatched]=useState<string[]>(()=>initial?.matched_pairs||[]);
  function chooseRight(pairId:string){if(!selected)return;if(selected!==pairId){setSelected('');emitDomeFeedback('wrong');return}const next=[...new Set([...matched,pairId])];setMatched(next);setSelected('');emitDomeFeedback('success');const completed=next.length===pairs.length;void onResult({completed,correct:completed,matched_pairs:next})}
  return <View testID='template-matching' style={{gap:8}}><Text style={{fontWeight:'800'}}>Соедини пары</Text><View style={{flexDirection:'row',gap:8}}><View style={{flex:1,gap:8}}>{pairs.map(pair=><Chip key={pair.id} testID={`match-left-${pair.id}`} value={pair.left} active={selected===pair.id} done={matched.includes(pair.id)} onPress={disabled?undefined:()=>setSelected(pair.id)}/>)}</View><View style={{flex:1,gap:8}}>{pairs.map(pair=><Chip key={pair.id} testID={`match-right-${pair.id}`} value={pair.right} done={matched.includes(pair.id)} onPress={disabled?undefined:()=>chooseRight(pair.id)}/>)}</View></View></View>;
}

function MemoryTask({slide,initial,onResult,disabled}:{slide:any;initial:any;onResult:(value:TemplateTaskResult)=>void;disabled:boolean}){
  const deck=useMemo(()=>memoryDeck(slide),[slide]);const[open,setOpen]=useState<string[]>([]);const[matched,setMatched]=useState<string[]>(()=>initial?.matched_pairs||[]);
  function reveal(cardId:string,pairId:string){if(open.includes(cardId)||matched.includes(pairId))return;if(open.length>=2)setOpen([cardId]);else if(open.length===0)setOpen([cardId]);else{const first=deck.find(card=>card.id===open[0]);if(first?.pairId===pairId){const next=[...new Set([...matched,pairId])];setMatched(next);setOpen([]);emitDomeFeedback('success');const completed=next.length===taskPairs(slide).length;void onResult({completed,correct:completed,matched_pairs:next})}else{setOpen([cardId]);emitDomeFeedback('wrong')}}}
  return <View testID='template-memory' style={{gap:8}}><Text style={{fontWeight:'800'}}>Найди одинаковые пары</Text><View style={{flexDirection:'row',flexWrap:'wrap',gap:8}}>{deck.map(card=>{const visible=open.includes(card.id)||matched.includes(card.pairId);return <DomePressable key={card.id} testID={`memory-${card.id}`} accessibilityRole='button' disabled={disabled||matched.includes(card.pairId)} onPress={()=>reveal(card.id,card.pairId)} style={{width:'22%',minWidth:64,aspectRatio:1,borderRadius:14,backgroundColor:visible?'#fff':'#246bfd',borderWidth:3,borderColor:matched.includes(card.pairId)?'#13a864':'#d3dae6'}} contentStyle={{padding:5}}><Text style={{fontWeight:'800',textAlign:'center',color:visible?'#20243a':'#fff'}}>{visible?card.value.label:'?'}</Text></DomePressable>})}</View></View>;
}

function PuzzleTask({slide,initial,onResult,disabled}:{slide:any;initial:any;onResult:(value:TemplateTaskResult)=>void;disabled:boolean}){
  const count=Math.max(2,Math.min(24,Number(slide?.pieces||slide?.piece_count||6)));const[order,setOrder]=useState<number[]>(()=>Array.isArray(initial?.piece_order)&&initial.piece_order.length===count?initial.piece_order:initialPuzzleOrder(count,String(slide?.slide_id||'puzzle')));const[selected,setSelected]=useState<number|null>(null);
  const columns=count<=6?3:count<=12?4:5;
  function choose(index:number){if(selected===null){setSelected(index);return}const next=swapPuzzlePieces(order,selected,index);setOrder(next);setSelected(null);const completed=puzzleSolved(next);if(completed)emitDomeFeedback('success');void onResult({completed,correct:completed,piece_order:next})}
  return <View testID='template-puzzle' style={{gap:8}}><Text style={{fontWeight:'800'}}>Собери части по порядку</Text><View style={{flexDirection:'row',flexWrap:'wrap',gap:5}}>{order.map((piece,index)=><DomePressable key={`${piece}-${index}`} testID={`puzzle-position-${index}`} accessibilityRole='button' disabled={disabled} onPress={()=>choose(index)} style={{width:`${Math.floor(96/columns)}%`,aspectRatio:1,borderRadius:10,borderWidth:3,borderColor:selected===index?'#246bfd':'#d3dae6',backgroundColor:'#eef4ff'}}><Text style={{fontSize:22,fontWeight:'900'}}>{piece+1}</Text></DomePressable>)}</View></View>;
}

function SequenceTask({slide,initial,onResult,disabled}:{slide:any;initial:any;onResult:(value:TemplateTaskResult)=>void;disabled:boolean}){
  const items=useMemo(()=>taskItems(slide),[slide]);const expected=items.map(item=>item.id);const[order,setOrder]=useState<string[]>(()=>Array.isArray(initial?.order)&&initial.order.length===items.length?initial.order:initialPuzzleOrder(items.length,String(slide?.slide_id||'sequence')).map(index=>expected[index]));
  function move(index:number,direction:-1|1){const next=moveSequenceItem(order,index,direction);setOrder(next);const completed=sequenceSolved(next,expected);void onResult({completed,correct:completed,order:next})}
  return <View testID='template-sequence' style={{gap:7}}><Text style={{fontWeight:'800'}}>Расставь по порядку</Text>{order.map((id,index)=>{const item=items.find(value=>value.id===id)!;return <View key={id} style={{flexDirection:'row',alignItems:'center',gap:7}}><Text style={{width:26,fontWeight:'900'}}>{index+1}.</Text><View style={{flex:1}}><Chip testID={`sequence-${id}`} value={item}/></View><DomePressable disabled={disabled||index===0} onPress={()=>move(index,-1)} style={{width:44,height:44}}><Text>↑</Text></DomePressable><DomePressable disabled={disabled||index===order.length-1} onPress={()=>move(index,1)} style={{width:44,height:44}}><Text>↓</Text></DomePressable></View>})}</View>;
}

export function LessonTaskRuntime({slide,initialResult,onResult,onDragging=()=>{},disabled=false}:{slide:any;initialResult?:any;onResult:(value:TemplateTaskResult)=>void|Promise<void>;onDragging?:(value:boolean)=>void;disabled?:boolean}){
  const type=canonicalTaskType(slide);const emit=(value:TemplateTaskResult)=>{void onResult(value)};
  useEffect(()=>()=>onDragging(false),[onDragging]);
  if(!isStableTaskTemplate(slide))return null;
  if(type==='drag_drop')return <DragDropTask slide={slide} initial={initialResult} onResult={emit} onDragging={onDragging} disabled={disabled}/>;
  if(type==='matching')return <MatchingTask slide={slide} initial={initialResult} onResult={emit} disabled={disabled}/>;
  if(type==='memory')return <MemoryTask slide={slide} initial={initialResult} onResult={emit} disabled={disabled}/>;
  if(type==='puzzle')return <PuzzleTask slide={slide} initial={initialResult} onResult={emit} disabled={disabled}/>;
  return <SequenceTask slide={slide} initial={initialResult} onResult={emit} disabled={disabled}/>;
}
