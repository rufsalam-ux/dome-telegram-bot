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

export const GIFT_OPTIONS:SelectableImageOption[]=[
  {id:'teddy',label:'Teddy bear',emoji:'🧸',rect:{left:0.19,top:0.045,width:0.14,height:0.23}},
  {id:'book',label:'Book',emoji:'📚',rect:{left:0.35,top:0.045,width:0.14,height:0.23}},
  {id:'flowers',label:'Flowers',emoji:'💐',rect:{left:0.51,top:0.045,width:0.14,height:0.23}},
  {id:'backpack',label:'Backpack',emoji:'🎒',rect:{left:0.67,top:0.045,width:0.14,height:0.23}},
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
  {id:'hat',label:'Hat',useful:true},
  {id:'boots',label:'Boots',useful:true},
  {id:'camera',label:'Camera',useful:true},
  {id:'gloves',label:'Gloves',useful:true},
  {id:'swimsuit',label:'Swimsuit',useful:false},
  {id:'flippers',label:'Flippers',useful:false},
  {id:'shorts',label:'Shorts',useful:false},
] as const;

// Text is spoken/rendered separately in the selected target language. These
// masks remove baked Russian instructions from old source illustrations.
export const LOCALIZED_IMAGE_MASKS:Record<string,NormalizedRect[]>={
  'slide-01.png':[{left:0.23,top:0.03,width:0.55,height:0.34}],
  'slide-03.png':[{left:0.17,top:0.01,width:0.66,height:0.3}],
  'slide-04.png':[{left:0.17,top:0.01,width:0.66,height:0.27}],
  'slide-06.png':[{left:0.21,top:0.01,width:0.58,height:0.3}],
  'slide-07.png':[{left:0.23,top:0.02,width:0.54,height:0.35},{left:0.04,top:0.34,width:0.9,height:0.2}],
  'slide-08.png':[{left:0.04,top:0.01,width:0.48,height:0.27},{left:0.04,top:0.28,width:0.42,height:0.22}],
  'slide-09.png':[{left:0.03,top:0.02,width:0.3,height:0.09}],
  'slide-18.png':[{left:0.15,top:0.01,width:0.7,height:0.16}],
  'slide-19.png':[{left:0.39,top:0.83,width:0.22,height:0.14}],
  'slide-21.png':[{left:0.17,top:0.37,width:0.66,height:0.13}],
  'slide-23.png':[{left:0.48,top:0.01,width:0.51,height:0.96}],
  'slide-40.png':[{left:0.17,top:0.37,width:0.66,height:0.44}],
  'slide-41.png':[{left:0.08,top:0.05,width:0.84,height:0.18}],
  'slide-48.png':[{left:0.1,top:0.01,width:0.61,height:0.15}],
};

export function buildRuntimeOrder(slides:any[]):any[]{
  const by:Record<string,any>={};slides.forEach(slide=>{by[slide.slide_id]=slide});
  const output:any[]=[];const seen=new Set<string>();let id='slide_01';
  while(id&&by[id]&&!seen.has(id)&&output.length<80){seen.add(id);output.push(by[id]);id=by[id].next_slide}
  return output;
}

export function selectedCardBranch(slides:any[],cardId:string):any|undefined{
  const index=CARD_OPTIONS.findIndex(option=>option.id===cardId);
  return index<0?undefined:slides.find(slide=>slide.slide_id===`slide_${10+index}`);
}

export function adaptivePrompt(slide:any,languageLevel:string|undefined):string{
  const level=String(languageLevel||'PRE_A1').toUpperCase();
  if((level==='PRE_A1'||level==='A1')&&slide.simplified_text)return slide.simplified_text;
  return slide.bot_says_target||slide.question||'';
}
