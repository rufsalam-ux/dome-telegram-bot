import * as SecureStore from 'expo-secure-store';
const BASE_URL=process.env.EXPO_PUBLIC_API_URL||'';
export class ApiError extends Error { status:number; constructor(status:number,message:string){super(message);this.status=status;} }
export async function api<T>(path:string,init:RequestInit={}):Promise<T>{
  if(!BASE_URL) throw new ApiError(0,'API_URL_NOT_CONFIGURED');
  const token=await SecureStore.getItemAsync('dome_access_token');
  const res=await fetch(`${BASE_URL}${path}`,{...init,headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{}) ,...(init.headers||{})}});
  const text=await res.text(); let data:any=null; try{data=text?JSON.parse(text):null}catch{data=text}
  if(!res.ok) throw new ApiError(res.status,data?.message||data?.error||`HTTP_${res.status}`);
  return data as T;
}
