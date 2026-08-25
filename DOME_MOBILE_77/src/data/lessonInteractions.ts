export type NormalizedRect={left:number;top:number;width:number;height:number};

export type SelectableImageOption={id:string;label:string;emoji?:string;rect:NormalizedRect};

export const CARD_OPTIONS:SelectableImageOption[]=[
  {id:'A',label:'Kite',rect:{left:0.115,top:0.18,width:0.24,height:0.31}},
  {id:'Б',label:'Friends',rect:{left:0.38,top:0.18,width:0.24,height:0.31}},
  {id:'В',label:'Lake',rect:{left:0.645,top:0.18,width:0.24,height:0.31}},
  {id:'Г',label:'Dog',rect:{left:0.115,top:0.58,width:0.24,height:0.31}},
  {id:'Д',label:'Rabbits',rect:{left:0.38,top:0.58,width:0.24,height:0.31}},
  {id:'Е',label:'Lion',rect:{left:0.645,top:0.58,width:0.24,height:0.31}},
];

export const ANIMAL_PAIR_OPTIONS:Record<string,SelectableImageOption[]>={
  penguin_parrot:[
    {id:'penguin',label:'Penguin',emoji:'🐧',rect:{left:0,top:0,width:0.5,height:1}},
    {id:'parrot',label:'Red parrot',emoji:'🦜',rect:{left:0.5,top:0,width:0.5,height:1}},
  ],
  lion_turtle:[
    {id:'lion',label:'Lion',emoji:'🦁',rect:{left:0,top:0,width:0.5,height:1}},
    {id:'turtle',label:'Turtle',emoji:'🐢',rect:{left:0.5,top:0,width:0.5,height:1}},
  ],
};

export const GIRAFFE_CHOICES=[
  {id:'elephant',label:'Elephant',emoji:'🐘'},
  {id:'giraffe',label:'Giraffe',emoji:'🦒'},
  {id:'polar_bear',label:'Polar bear',emoji:'🐻‍❄️'},
];

export const MOOD_EMOJIS=['🙂','😟','😁','😄','😍','😌'];

export const VOICE_EXAMPLES_RU:Record<string,string>={
  lesha_clothes:'Почему ты так тепло одет?',
  mila_gift:'Мила подарила мне подарок.',
  take_trip:'Я возьму куртку.',
  polar_bear:'Белый медведь большой.',
  lion:'Лев сильный.',
  parrot:'Попугай красный и красивый.',
  giraffe:'Жираф высокий. У него длинная шея.',
  penguin:'Я вижу пингвина.',
  zebra:'Зебра полосатая.',
  invite:'Приезжайте ко мне!',
};

export const SUITCASE_ITEMS=[
  {id:'jacket',label:'Jacket',useful:true},
  {id:'binoculars',label:'Binoculars',useful:true},
  {id:'water',label:'Water bottle',useful:true},
  {id:'compass',label:'Compass',useful:true},
  {id:'teddy',label:'Teddy bear',useful:false},
  {id:'camera',label:'Camera',useful:true},
  {id:'telescope',label:'Telescope',useful:true},
  {id:'fish',label:'Fish',useful:false},
  {id:'notebook',label:'Notebook',useful:true},
  {id:'sunglasses',label:'Sunglasses',useful:true},
] as const;

export function buildRuntimeOrder(slides:any[]):any[]{
  const ordered=[...slides].sort((a,b)=>(Number(a?.order)||9999)-(Number(b?.order)||9999));
  if(!ordered.some(slide=>String(slide?.next_slide||'').trim()))return ordered;
  const by:Record<string,any>={};ordered.forEach(slide=>{by[slide.slide_id]=slide});
  const output:any[]=[];const seen=new Set<string>();let id=String(ordered.find(slide=>slide?.entry===true)?.slide_id||(by.slide_01?'slide_01':ordered[0]?.slide_id)||'');
  while(id&&by[id]&&!seen.has(id)&&output.length<80){seen.add(id);output.push(by[id]);id=by[id].next_slide}
  return output;
}

export function selectedCardBranch(slides:any[],cardId:string):any|undefined{
  const index=CARD_OPTIONS.findIndex(option=>option.id===cardId);
  return index<0?undefined:slides.find(slide=>slide.slide_id===`slide_${10+index}`);
}
