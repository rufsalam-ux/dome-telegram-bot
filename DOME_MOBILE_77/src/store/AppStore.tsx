import React,{createContext,useCallback,useContext,useEffect,useMemo,useState} from 'react';
import {ChildProfile,ParentProfile,Screen,UiLanguage} from '../types/domain';
import {clearApiToken,onApiSessionInvalidated,persistApiToken} from '../api/mobile';

interface State{
  screen:Screen;
  uiLanguage:UiLanguage;
  parent?:ParentProfile;
  children:ChildProfile[];
  selectedChild?:ChildProfile;
  token:string;
  isAdmin:boolean;
  consentsAccepted:boolean;
  setScreen:(screen:Screen)=>void;
  setUiLanguage:(language:UiLanguage)=>void;
  setSelectedChild:(child?:ChildProfile)=>void;
  setConsentsAccepted:(accepted:boolean)=>void;
  hydrate:(data:any,token:string)=>Promise<void>;
  addChild:(child:ChildProfile)=>void;
  updateChild:(child:ChildProfile)=>void;
  logout:()=>Promise<void>;
}

const Ctx=createContext<State|null>(null);

function childProfile(raw:any,parentId:string):ChildProfile{
  return {
    id:String(raw.id),
    parentId,
    name:String(raw.name||raw.display_name).trim(),
    age:raw.age??raw.age_years,
    learningLanguage:raw.target_language||'ru',
    nativeLanguage:raw.native_language||'ru',
    languageLevel:raw.language_level||'PRE_A1',
    workingDifficulty:Number(raw.working_difficulty??0.15),
    courseId:'conversation',
    activeCharacterId:raw.active_character_id,
    heroUrl:raw.hero_url||null,
  };
}

export function AppStoreProvider({children}:{children:React.ReactNode}){
  const[screen,setScreen]=useState<Screen>('auth');
  const[uiLanguage,setUiLanguage]=useState<UiLanguage>('ru');
  const[parent,setParent]=useState<ParentProfile|undefined>();
  const[childrenList,setChildren]=useState<ChildProfile[]>([]);
  const[selectedChild,setSelectedChild]=useState<ChildProfile|undefined>();
  const[token,setToken]=useState('');
  const[consentsAccepted,setConsentsAccepted]=useState(true);

  const clearLocalSession=useCallback(()=>{
    setToken('');
    setParent(undefined);
    setChildren([]);
    setSelectedChild(undefined);
    setScreen('auth');
  },[]);

  useEffect(()=>onApiSessionInvalidated(clearLocalSession),[clearLocalSession]);

  const hydrate=useCallback(async(data:any,nextToken:string)=>{
    if(!data?.parent||!Array.isArray(data.children)){
      throw new Error('Сервер вернул неполные данные сессии');
    }
    const parentId=String(data.parent.id||'');
    const profiles=data.children
      .filter((item:any)=>item&&item.id!=null&&String(item.name||item.display_name||'').trim())
      .map((item:any)=>childProfile(item,parentId));

    await persistApiToken(nextToken);
    setToken(nextToken);
    setParent(data.parent);
    setChildren(profiles);
    setSelectedChild(undefined);
    setScreen('children');
  },[]);

  const addChild=useCallback((child:ChildProfile)=>{
    setChildren(items=>items.some(item=>item.id===child.id)?items.map(item=>item.id===child.id?child:item):[...items,child]);
  },[]);

  const updateChild=useCallback((child:ChildProfile)=>{
    setSelectedChild(child);
    setChildren(items=>items.map(item=>item.id===child.id?child:item));
  },[]);

  const logout=useCallback(async()=>{
    await clearApiToken();
    clearLocalSession();
  },[clearLocalSession]);

  const value=useMemo(()=>({
    screen,uiLanguage,parent,children:childrenList,selectedChild,token,isAdmin:false,consentsAccepted,
    setScreen,setUiLanguage,setSelectedChild,setConsentsAccepted,hydrate,addChild,updateChild,logout,
  }),[screen,uiLanguage,parent,childrenList,selectedChild,token,consentsAccepted,hydrate,addChild,updateChild,logout]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppStore(){
  const value=useContext(Ctx);
  if(!value)throw new Error('Store missing');
  return value;
}
