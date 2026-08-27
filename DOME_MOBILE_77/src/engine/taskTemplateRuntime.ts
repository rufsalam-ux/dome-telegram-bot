export type TaskOption={id:string;label:string;asset?:string};
export type TaskPair={id:string;left:TaskOption;right:TaskOption};

const ALIASES:Record<string,string>={multiple_choice:'choice',ordering:'sequence'};
const TEMPLATE_TYPES=new Set(['drag_drop','matching','memory','puzzle','sequence']);

export function canonicalTaskType(slide:any):string{
  const raw=String(slide?.type||'').trim().toLowerCase();
  return ALIASES[raw]||raw;
}

export function isStableTaskTemplate(slide:any):boolean{
  return TEMPLATE_TYPES.has(canonicalTaskType(slide))&&slide?.interactive_task!=='suitcase';
}

function option(value:any,index:number,prefix:string):TaskOption{
  if(typeof value==='string'||typeof value==='number')return {id:`${prefix}_${index+1}`,label:String(value)};
  return {
    id:String(value?.id||`${prefix}_${index+1}`),
    label:String(value?.label||value?.text||value?.word||value?.name||value?.id||`${index+1}`),
    asset:value?.asset||value?.image||value?.src,
  };
}

export function taskPairs(slide:any):TaskPair[]{
  return (Array.isArray(slide?.pairs)?slide.pairs:[]).map((value:any,index:number)=>{
    if(Array.isArray(value))return {id:`pair_${index+1}`,left:option(value[0],index,'left'),right:option(value[1],index,'right')};
    return {
      id:String(value?.id||`pair_${index+1}`),
      left:option(value?.left??value?.a??value?.first,index,'left'),
      right:option(value?.right??value?.b??value?.second,index,'right'),
    };
  });
}

export function taskItems(slide:any):TaskOption[]{
  return (Array.isArray(slide?.items)?slide.items:[]).map((value:any,index:number)=>option(value,index,'item'));
}

export function taskTargets(slide:any):TaskOption[]{
  return (Array.isArray(slide?.targets)?slide.targets:[]).map((value:any,index:number)=>option(value,index,'target'));
}

export function expectedTargetId(slide:any,itemId:string,itemIndex:number):string{
  const item=(slide?.items||[])[itemIndex];
  return String(item?.target_id||item?.targetId||taskTargets(slide)[itemIndex]?.id||itemId);
}

function hash(value:string):number{
  let state=2166136261;for(let index=0;index<value.length;index++){state^=value.charCodeAt(index);state=Math.imul(state,16777619)}return state>>>0;
}

export function deterministicShuffle<T>(values:T[],seed:string):T[]{
  const out=[...values];let state=hash(seed)||1;
  for(let index=out.length-1;index>0;index--){state=(Math.imul(state,1664525)+1013904223)>>>0;const target=state%(index+1);const held=out[index]!;out[index]=out[target]!;out[target]=held}
  return out;
}

export type MemoryCard={id:string;pairId:string;side:'left'|'right';value:TaskOption};

export function memoryDeck(slide:any):MemoryCard[]{
  const cards=taskPairs(slide).flatMap(pair=>([
    {id:`${pair.id}:left`,pairId:pair.id,side:'left' as const,value:pair.left},
    {id:`${pair.id}:right`,pairId:pair.id,side:'right' as const,value:pair.right},
  ]));
  return deterministicShuffle(cards,String(slide?.slide_id||'memory'));
}

export function initialPuzzleOrder(pieceCount:number,seed:string):number[]{
  const size=Math.max(2,Math.min(24,Math.trunc(pieceCount)||6));const solved=Array.from({length:size},(_,index)=>index);
  const shuffled=deterministicShuffle(solved,seed);
  return shuffled.every((value,index)=>value===index)?[...shuffled.slice(1),shuffled[0]!]:shuffled;
}

export function swapPuzzlePieces(order:number[],first:number,second:number):number[]{
  if(first<0||second<0||first>=order.length||second>=order.length||first===second)return order;
  const next=[...order];const held=next[first]!;next[first]=next[second]!;next[second]=held;return next;
}

export function puzzleSolved(order:number[]):boolean{return order.every((value,index)=>value===index)}

export function moveSequenceItem(order:string[],index:number,direction:-1|1):string[]{
  const target=index+direction;if(index<0||index>=order.length||target<0||target>=order.length)return order;
  const next=[...order];const held=next[index]!;next[index]=next[target]!;next[target]=held;return next;
}

export function sequenceSolved(order:string[],expected:string[]):boolean{
  return order.length===expected.length&&order.every((value,index)=>value===expected[index]);
}
