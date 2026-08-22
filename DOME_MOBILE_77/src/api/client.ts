import {API_BASE,restoreApiToken} from './mobile';
const BASE_URL=process.env.EXPO_PUBLIC_API_URL||API_BASE;
export class ApiError extends Error { status:number; constructor(status:number,message:string){super(message);this.status=status;} }
export async function api<T>(path:string,init:RequestInit={}):Promise<T>{
  const token=await restoreApiToken();
  const res=await fetch(`${BASE_URL}${path}`,{...init,headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{}) ,...(init.headers||{})}});
  const text=await res.text(); let data:any=null; try{data=text?JSON.parse(text):null}catch{data=text}
  if(!res.ok) throw new ApiError(res.status,data?.message||data?.error||`HTTP_${res.status}`);
  return data as T;
}
