import {Vibration} from 'react-native';

export type ExperienceEvent=
  |'BUTTON_TAP'|'CORRECT'|'EXCELLENT'|'TRY_AGAIN'|'DRAG_PICKUP'
  |'DROP_CORRECT'|'PUZZLE_SNAP'|'TASK_COMPLETE'|'LESSON_COMPLETE'
  |'CAT_APPEAR'|'CAT_ACTION'|'WORLD_TRANSITION'|'MOVIE_START';

export type ExperiencePreferences={soundEffects:boolean;haptics:boolean};

const STORAGE_KEY='dome_experience_preferences_v1';
const DEFAULTS:ExperiencePreferences={soundEffects:true,haptics:true};
const clickAsset=require('../../assets/sounds/soft-click.wav');
const successAsset=require('../../assets/sounds/suitcase-pop.wav');
const gentleAsset=require('../../assets/sounds/suitcase-return.wav');

let preferences={...DEFAULTS};
let loaded=false;
let loadPromise:Promise<void>|null=null;
const listeners=new Set<(value:ExperiencePreferences)=>void>();
const players:Record<string,any>={};

function secureStore():any{
  try{return require('expo-secure-store')}catch(error){console.warn('DOME_EXPERIENCE_STORAGE_UNAVAILABLE',error);return null}
}

export async function loadExperiencePreferences():Promise<ExperiencePreferences>{
  if(!loadPromise)loadPromise=(async()=>{
    try{
      const store=secureStore();const raw=store?await store.getItemAsync(STORAGE_KEY):null;
      if(raw){const parsed=JSON.parse(raw);preferences={soundEffects:parsed.soundEffects!==false,haptics:parsed.haptics!==false}}
    }catch(error){console.warn('DOME_EXPERIENCE_LOAD_FAILED',error)}finally{loaded=true;listeners.forEach(listener=>listener({...preferences}))}
  })();
  await loadPromise;return {...preferences};
}

export async function setExperiencePreferences(update:Partial<ExperiencePreferences>):Promise<ExperiencePreferences>{
  preferences={...preferences,...update};loaded=true;listeners.forEach(listener=>listener({...preferences}));
  try{const store=secureStore();if(store)await store.setItemAsync(STORAGE_KEY,JSON.stringify(preferences))}catch(error){console.warn('DOME_EXPERIENCE_SAVE_FAILED',error)}
  return {...preferences};
}

export function subscribeExperiencePreferences(listener:(value:ExperiencePreferences)=>void):()=>void{
  listeners.add(listener);listener({...preferences});return()=>listeners.delete(listener);
}

function vibrationFor(event:ExperienceEvent):number|number[]{
  if(event==='EXCELLENT'||event==='LESSON_COMPLETE')return [0,18,38,24];
  if(event==='TASK_COMPLETE'||event==='MOVIE_START'||event==='WORLD_TRANSITION')return [0,12,32,18];
  if(event==='DROP_CORRECT'||event==='PUZZLE_SNAP')return 18;
  if(event==='TRY_AGAIN')return [0,6,28,6];
  if(event==='DRAG_PICKUP'||event==='BUTTON_TAP'||event==='CAT_ACTION')return 8;
  return 12;
}

function assetFor(event:ExperienceEvent):any{
  if(event==='TRY_AGAIN'||event==='WORLD_TRANSITION')return gentleAsset;
  if(['CORRECT','EXCELLENT','DROP_CORRECT','PUZZLE_SNAP','TASK_COMPLETE','LESSON_COMPLETE','MOVIE_START'].includes(event))return successAsset;
  return clickAsset;
}

async function playSound(event:ExperienceEvent):Promise<void>{
  try{
    const asset=assetFor(event);const key=String(asset);let player=players[key];
    if(!player){const {createAudioPlayer}=require('expo-audio');player=createAudioPlayer(asset,{keepAudioSessionActive:true});players[key]=player}
    await player.seekTo(0);player.play();
  }catch(error){console.warn('DOME_EXPERIENCE_AUDIO_FAILED',{event,error})}
}

export function playExperience(event:ExperienceEvent):void{
  void (async()=>{
    if(!loaded)await loadExperiencePreferences();
    if(preferences.haptics)try{Vibration.vibrate(vibrationFor(event))}catch(error){console.warn('DOME_EXPERIENCE_HAPTIC_FAILED',{event,error})}
    if(preferences.soundEffects)await playSound(event);
  })();
}
