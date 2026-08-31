import {Vibration} from 'react-native';

export type ExperienceEvent=
  |'BUTTON_TAP'|'BUTTON_CONTINUE'|'RECORDING_START'|'CORRECT'|'EXCELLENT'|'TRY_AGAIN'|'DRAG_PICKUP'
  |'RECORDING_STOP'
  |'DRAG_TEXTURE'|'DROP_CORRECT'|'DROP_INVALID'|'PUZZLE_SNAP'|'TASK_COMPLETE'|'LESSON_COMPLETE'
  |'CAT_APPEAR'|'CAT_ACTION'|'WORLD_TRANSITION'|'MOVIE_START';

export type ExperiencePreferences={soundEffects:boolean;haptics:boolean;uiSoundVolume:number};
export type ExperienceFeedbackOptions={sound?:boolean;haptics?:boolean};

const STORAGE_KEY='dome_experience_preferences_v1';
// Verified against the bundled WAV peaks: this remains gentle, but no longer
// pushes the short UI cues below a typical phone speaker's audible range.
export const UI_SOUND_VOLUME=.26;
export const UI_SOUNDS_ENABLED=true;
export const HAPTICS_ENABLED=true;
export const DEFAULT_UI_SOUND_VOLUME=UI_SOUND_VOLUME;
export const DOME_AUDIO_CHANNELS={speech:'AI_LESSON_SPEECH',effects:'UI_EFFECTS'} as const;
const DEFAULTS:ExperiencePreferences={soundEffects:UI_SOUNDS_ENABLED,haptics:HAPTICS_ENABLED,uiSoundVolume:UI_SOUND_VOLUME};
const clickAsset=require('../../assets/sounds/soft-click.wav');
const successAsset=require('../../assets/sounds/suitcase-pop.wav');
const gentleAsset=require('../../assets/sounds/suitcase-return.wav');

let preferences={...DEFAULTS};
let loaded=false;
let loadPromise:Promise<void>|null=null;
const listeners=new Set<(value:ExperiencePreferences)=>void>();
const players:Record<string,any>={};
const audioSuppressions=new Set<string>();

function secureStore():any{
  try{return require('expo-secure-store')}catch(error){console.warn('DOME_EXPERIENCE_STORAGE_UNAVAILABLE',error);return null}
}

export async function loadExperiencePreferences():Promise<ExperiencePreferences>{
  if(!loadPromise)loadPromise=(async()=>{
    try{
      const store=secureStore();const raw=store?await store.getItemAsync(STORAGE_KEY):null;
      if(raw){const parsed=JSON.parse(raw);const volume=Number(parsed.uiSoundVolume);preferences={soundEffects:parsed.soundEffects!==false,haptics:parsed.haptics!==false,uiSoundVolume:Number.isFinite(volume)?Math.max(0,Math.min(.45,volume)):DEFAULT_UI_SOUND_VOLUME}}
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
  if(event==='DRAG_TEXTURE')return 3;
  if(event==='DROP_INVALID')return [0,5,24,5];
  if(event==='TRY_AGAIN')return [0,6,28,6];
  if(event==='DRAG_PICKUP'||event==='BUTTON_TAP'||event==='CAT_ACTION')return 8;
  if(event==='BUTTON_CONTINUE'||event==='RECORDING_START'||event==='RECORDING_STOP')return 9;
  return 12;
}

function assetFor(event:ExperienceEvent):any{
  if(event==='TRY_AGAIN'||event==='DROP_INVALID'||event==='WORLD_TRANSITION'||event==='RECORDING_STOP')return gentleAsset;
  if(['BUTTON_CONTINUE','CORRECT','EXCELLENT','DROP_CORRECT','PUZZLE_SNAP','TASK_COMPLETE','LESSON_COMPLETE','MOVIE_START'].includes(event))return successAsset;
  return clickAsset;
}

function soundGainFor(event:ExperienceEvent):number{
  return event==='DRAG_TEXTURE'?.32:1;
}

async function playSound(event:ExperienceEvent):Promise<void>{
  try{
    if(audioSuppressions.size>0)return;
    const asset=assetFor(event);const key=String(asset);let player=players[key];
    if(!player){const {createAudioPlayer}=require('expo-audio');player=createAudioPlayer(asset,{keepAudioSessionActive:true});players[key]=player}
    player.volume=Math.max(0,Math.min(.45,preferences.uiSoundVolume*soundGainFor(event)));await player.seekTo(0);player.play();
  }catch(error){console.warn('DOME_EXPERIENCE_AUDIO_FAILED',{event,error})}
}

export function setExperienceAudioSuppressed(reason:string,suppressed:boolean):void{
  if(suppressed)audioSuppressions.add(reason);else audioSuppressions.delete(reason);
}

export async function playRecordingBoundaryCue(event:'RECORDING_START'|'RECORDING_STOP'):Promise<void>{
  if(!loaded)await loadExperiencePreferences();
  if(!preferences.soundEffects||audioSuppressions.size>0)return;
  await playSound(event);
  // The bundled cue is deliberately tiny. Let it leave the output buffer
  // before a recording session starts so it cannot enter the child's take.
  await new Promise(resolve=>setTimeout(resolve,110));
}

export function playExperience(event:ExperienceEvent,options:ExperienceFeedbackOptions={}):void{
  void (async()=>{
    if(!loaded)await loadExperiencePreferences();
    if(options.haptics!==false&&preferences.haptics)try{Vibration.vibrate(vibrationFor(event))}catch(error){console.warn('DOME_EXPERIENCE_HAPTIC_FAILED',{event,error})}
    if(options.sound!==false&&preferences.soundEffects)await playSound(event);
  })();
}
