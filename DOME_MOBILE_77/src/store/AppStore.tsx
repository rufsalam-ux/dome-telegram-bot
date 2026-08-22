import React,{createContext,useContext,useMemo,useState} from 'react';
import * as SecureStore from 'expo-secure-store';
import {ChildProfile,ParentProfile,Screen,UiLanguage} from '../types/domain';
import {setApiToken} from '../api/mobile';
interface State{screen:Screen;uiLanguage:UiLanguage;parent?:ParentProfile;children:ChildProfile[];selectedChild?:ChildProfile;token:string;isAdmin:boolean;consentsAccepted:boolean;setScreen:(s:Screen)=>void;setUiLanguage:(l:UiLanguage)=>void;setSelectedChild:(c?:ChildProfile)=>void;setConsentsAccepted:(v:boolean)=>void;hydrate:(data:any,token:string)=>Promise<void>;addChild:(c:ChildProfile)=>void;updateChild:(c:ChildProfile)=>void;logout:()=>Promise<void>}
const Ctx=createContext<State|null>(null);
export function AppStoreProvider({children}:{children:React.ReactNode}){
 const[screen,setScreen]=useState<Screen>('auth');const[uiLanguage,setUiLanguage]=useState<UiLanguage>('ru');const[parent,setParent]=useState<ParentProfile|undefined>();const[childrenList,setChildren]=useState<ChildProfile[]>([]);const[selectedChild,setSelectedChild]=useState<ChildProfile|undefined>();const[token,setToken]=useState('');const[consentsAccepted,setConsentsAccepted]=useState(true);
 const hydrate=async(data:any,t:string)=>{setApiToken(t);setToken(t);await SecureStore.setItemAsync('dome_mobile_token',t);setParent(data.parent);const cs=(data.children||[]).map((x:any)=>({id:String(x.id),parentId:String(data.parent?.id||''),name:x.name||x.display_name,age:x.age??x.age_years,learningLanguage:x.target_language||'ru',nativeLanguage:x.native_language||'ru',courseId:'conversation',activeCharacterId:x.active_character_id,heroUrl:x.hero_url||null}));setChildren(cs);setSelectedChild(undefined);setScreen('children')};
 const addChild=(c:ChildProfile)=>setChildren(xs=>[...xs,c]);
 const updateChild=(c:ChildProfile)=>{setSelectedChild(c);setChildren(xs=>xs.map(x=>x.id===c.id?c:x))};
 const logout=async()=>{await SecureStore.deleteItemAsync('dome_mobile_token');setApiToken('');setToken('');setParent(undefined);setChildren([]);setSelectedChild(undefined);setScreen('auth')};
 const value=useMemo(()=>({screen,uiLanguage,parent,children:childrenList,selectedChild,token,isAdmin:false,consentsAccepted,setScreen,setUiLanguage,setSelectedChild,setConsentsAccepted,hydrate,addChild,updateChild,logout}),[screen,uiLanguage,parent,childrenList,selectedChild,token,consentsAccepted]);return <Ctx.Provider value={value}>{children}</Ctx.Provider>}
export function useAppStore(){const v=useContext(Ctx);if(!v)throw new Error('Store missing');return v}
