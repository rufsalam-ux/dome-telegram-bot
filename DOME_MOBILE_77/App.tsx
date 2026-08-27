import React, { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { LogBox, Pressable, StatusBar, Text, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { logStartupStage, rootRuntimeFailure, StartupFailure } from './src/engine/startup';

const BUILD_COMMIT=(process.env.EXPO_PUBLIC_BUILD_COMMIT||'unmarked').trim();
const BUILD_TIMESTAMP=(process.env.EXPO_PUBLIC_BUILD_TIMESTAMP||'unknown-time').trim();
export const BUILD_MARKER=`BUILD ${BUILD_COMMIT.slice(0,12)} / ${BUILD_TIMESTAMP}`;

function createLazyAppRuntime(){return React.lazy(()=>import('./src/AppRuntime').then(module=>{
  logStartupStage('APP_RUNTIME_LOADED');return {default:module.AppRuntime};
}).catch(error=>{logStartupStage('APP_RUNTIME_LOAD_FAILED',{error_name:error instanceof Error?error.name:'UnknownError',error_message:error instanceof Error?error.message.slice(0,240):''});throw error}))}

// Expo Go can briefly lose its development websocket while the production API
// remains healthy. Keep the warning in Metro logs, but never cover the child's
// QA screen with an unrelated development toast.
LogBox.ignoreLogs(['Cannot connect to Expo CLI']);
if (!__DEV__) LogBox.ignoreAllLogs(true);

function BuildMarker({fatal=false}:{fatal?:boolean}){
  return <View pointerEvents='none' accessibilityLabel={BUILD_MARKER} style={fatal?{alignSelf:'flex-end',marginRight:6,marginBottom:3,borderRadius:5,backgroundColor:'rgba(0,0,0,.66)',paddingHorizontal:5,paddingVertical:2}:{position:'absolute',right:6,bottom:3,zIndex:9999,borderRadius:5,backgroundColor:'rgba(0,0,0,.66)',paddingHorizontal:5,paddingVertical:2}}><Text testID='dome-build-marker' style={{color:'#fff',fontSize:9,fontWeight:'700'}}>{BUILD_MARKER}</Text></View>;
}

type RootErrorBoundaryProps={children:React.ReactNode;onRetryPress:()=>void;retryTapCount:number;retrying:boolean};

class RootErrorBoundary extends React.Component<RootErrorBoundaryProps,{error:Error|null,failure:StartupFailure|null}>{
  state:{error:Error|null,failure:StartupFailure|null}={error:null,failure:null};
  static getDerivedStateFromError(error:Error){return {error,failure:rootRuntimeFailure(error)}}
  componentDidCatch(error:Error,info:React.ErrorInfo){const failure=rootRuntimeFailure(error);console.error('DOME_ROOT_RUNTIME_ERROR',failure.code,failure.reason,error,info.componentStack);logStartupStage('ROOT_RUNTIME_FAILED',{error_code:failure.code,error_name:error.name,error_reason:failure.reason,component_stack:info.componentStack?.slice(0,500)||''})}
  render(){
    if(this.state.error){const failure=this.state.failure||rootRuntimeFailure(this.state.error);return <View testID='fatal-startup-screen' pointerEvents='auto' style={{flex:1,backgroundColor:'#F7F7FB'}}><View pointerEvents='box-none' style={{flex:1,alignItems:'center',justifyContent:'center',padding:28}}><Text style={{fontSize:34,fontWeight:'900',color:'#20243A'}}>DOME</Text><Text style={{fontSize:17,textAlign:'center',marginVertical:14,color:'#42475C'}}>{failure.message}</Text><Text testID='startup-error-code' selectable style={{fontSize:12,textAlign:'center',marginBottom:5,color:'#6A7088'}}>ERROR CODE: {failure.code}</Text><Text testID='startup-error-reason' selectable style={{fontSize:11,textAlign:'center',marginBottom:8,color:'#6A7088'}}>{failure.reason}</Text><Text testID='retry-tap-count' style={{fontSize:14,fontWeight:'800',marginBottom:12,color:'#20243A'}}>RETRY TAP: {this.props.retryTapCount}</Text><Pressable testID='fatal-retry-button' accessibilityRole='button' accessibilityLabel='Повторить запуск DOME' accessibilityState={{disabled:false}} pointerEvents='auto' collapsable={false} hitSlop={12} disabled={false} onPress={this.props.onRetryPress} style={({pressed})=>({minHeight:56,minWidth:200,borderRadius:18,backgroundColor:pressed?'#174fc2':'#246bfd',alignItems:'center',justifyContent:'center',paddingHorizontal:24,zIndex:2,elevation:4})}><Text style={{color:'#fff',fontSize:18,fontWeight:'800'}}>{this.props.retrying?'Повторяем…':'Повторить'}</Text></Pressable></View><BuildMarker fatal/></View>}
    return <View style={{flex:1}}>{this.props.children}<BuildMarker/></View>;
  }
}

export default function App() {
  const[runtimeAttempt,setRuntimeAttempt]=useState(0);
  const[retryTapCount,setRetryTapCount]=useState(0);
  const[retrying,setRetrying]=useState(false);
  const retryTapCountRef=useRef(0);
  const retryTimerRef=useRef<ReturnType<typeof setTimeout>|null>(null);
  const LazyAppRuntime=useMemo(createLazyAppRuntime,[runtimeAttempt]);
  useEffect(()=>{logStartupStage('APP_MOUNT');return()=>{if(retryTimerRef.current!==null)clearTimeout(retryTimerRef.current)}},[]);
  const handleRetryPress=()=>{
    const nextTap=retryTapCountRef.current+1;retryTapCountRef.current=nextTap;
    console.log('RETRY_PRESS_RECEIVED',{retry_tap:nextTap,runtime_attempt:runtimeAttempt});
    setRetryTapCount(nextTap);setRetrying(true);
    try{logStartupStage('ROOT_RUNTIME_RETRY',{retry_tap:nextTap,runtime_attempt:runtimeAttempt})}catch{}
    if(retryTimerRef.current===null)retryTimerRef.current=setTimeout(()=>{
      retryTimerRef.current=null;setRuntimeAttempt(value=>value+1);setRetrying(false);
    },350);
  };
  return (
    <RootErrorBoundary key={runtimeAttempt} onRetryPress={handleRetryPress} retryTapCount={retryTapCount} retrying={retrying}>
      <SafeAreaProvider>
        <SafeAreaView edges={['top', 'left', 'right']} style={{ flex: 1, backgroundColor: '#F7F7FB' }}>
          <StatusBar barStyle="dark-content" />
          <Suspense fallback={<View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Text style={{fontSize:18,color:'#42475C'}}>Открываем DOME…</Text></View>}><LazyAppRuntime key={runtimeAttempt}/></Suspense>
        </SafeAreaView>
      </SafeAreaProvider>
    </RootErrorBoundary>
  );
}
