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

export interface SuitcaseItemDef {
  id: string;
  labelNominative: string;
  labelAccusative: string;
  label_ru: string;
  label_ru_accusative: string;
  label: string;
  label_en: string;
}

export const SUITCASE_ITEMS: readonly SuitcaseItemDef[] = [
  { id: 'jacket', labelNominative: 'куртка', labelAccusative: 'куртку', label_ru: 'куртка', label_ru_accusative: 'куртку', label: 'Jacket', label_en: 'Jacket' },
  { id: 'binoculars', labelNominative: 'бинокль', labelAccusative: 'бинокль', label_ru: 'бинокль', label_ru_accusative: 'бинокль', label: 'Binoculars', label_en: 'Binoculars' },
  { id: 'water', labelNominative: 'бутылка воды', labelAccusative: 'бутылку воды', label_ru: 'бутылка воды', label_ru_accusative: 'бутылку воды', label: 'Water bottle', label_en: 'Water bottle' },
  { id: 'compass', labelNominative: 'компас', labelAccusative: 'компас', label_ru: 'компас', label_ru_accusative: 'компас', label: 'Compass', label_en: 'Compass' },
  { id: 'teddy', labelNominative: 'мишка', labelAccusative: 'мишку', label_ru: 'мишка', label_ru_accusative: 'мишку', label: 'Teddy bear', label_en: 'Teddy bear' },
  { id: 'camera', labelNominative: 'фотоаппарат', labelAccusative: 'фотоаппарат', label_ru: 'фотоаппарат', label_ru_accusative: 'фотоаппарат', label: 'Camera', label_en: 'Camera' },
  { id: 'telescope', labelNominative: 'телескоп', labelAccusative: 'телескоп', label_ru: 'телескоп', label_ru_accusative: 'телескоп', label: 'Telescope', label_en: 'Telescope' },
  { id: 'fish', labelNominative: 'рыба', labelAccusative: 'рыбу', label_ru: 'рыба', label_ru_accusative: 'рыбу', label: 'Fish', label_en: 'Fish' },
  { id: 'notebook', labelNominative: 'блокнот', labelAccusative: 'блокнот', label_ru: 'блокнот', label_ru_accusative: 'блокнот', label: 'Notebook', label_en: 'Notebook' },
  { id: 'sunglasses', labelNominative: 'солнцезащитные очки', labelAccusative: 'солнцезащитные очки', label_ru: 'солнцезащитные очки', label_ru_accusative: 'солнцезащитные очки', label: 'Sunglasses', label_en: 'Sunglasses' },
];

export function buildRuntimeOrder(slides:any[]):any[]{
  // Disabled Studio steps are authoring data, not child progress steps.
  const ordered=slides.filter(slide=>slide?.enabled!==false).sort((a,b)=>(Number(a?.order)||9999)-(Number(b?.order)||9999));
  const nextId=(slide:any)=>String(slide?.next_slide||slide?.next_step_id||slide?.next||'').trim();
  if(!ordered.some(slide=>nextId(slide)))return ordered;
  const by:Record<string,any>={};ordered.forEach(slide=>{by[slide.slide_id]=slide});
  const output:any[]=[];const seen=new Set<string>();let id=String(ordered.find(slide=>slide?.entry===true)?.slide_id||(by.slide_01?'slide_01':ordered[0]?.slide_id)||'');
  while(id){if(seen.has(id))throw new Error(`LESSON_SEQUENCE_CYCLE:${id}`);if(!by[id])throw new Error(`LESSON_SEQUENCE_MISSING_STEP:${id}`);seen.add(id);output.push(by[id]);id=nextId(by[id])}
  return output;
}

export function selectedCardBranch(slides:any[],cardId:string):any|undefined{
  const index=CARD_OPTIONS.findIndex(option=>option.id===cardId);
  return index<0?undefined:slides.find(slide=>slide.slide_id===`slide_${10+index}`);
}
