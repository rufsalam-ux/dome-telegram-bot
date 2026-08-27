import React, { Suspense, useEffect, useMemo, useState } from 'react';
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

class RootErrorBoundary extends React.Component<{children:React.ReactNode,onRetry:()=>void},{error:Error|null,failure:StartupFailure|null}>{
  state:{error:Error|null,failure:StartupFailure|null}={error:null,failure:null};
  static getDerivedStateFromError(error:Error){return {error,failure:rootRuntimeFailure(error)}}
  componentDidCatch(error:Error,info:React.ErrorInfo){const failure=rootRuntimeFailure(error);console.error('DOME_ROOT_RUNTIME_ERROR',failure.code,failure.reason,error,info.componentStack);logStartupStage('ROOT_RUNTIME_FAILED',{error_code:failure.code,error_name:error.name,error_reason:failure.reason,component_stack:info.componentStack?.slice(0,500)||''})}
  render(){
    if(this.state.error){const failure=this.state.failure||rootRuntimeFailure(this.state.error);return <View style={{flex:1,alignItems:'center',justifyContent:'center',padding:28,backgroundColor:'#F7F7FB'}}><Text style={{fontSize:34,fontWeight:'900',color:'#20243A'}}>DOME</Text><Text style={{fontSize:17,textAlign:'center',marginVertical:14,color:'#42475C'}}>{failure.message}</Text><Text testID='startup-error-code' selectable style={{fontSize:12,textAlign:'center',marginBottom:14,color:'#6A7088'}}>Код: {failure.code} · {failure.reason}</Text><Pressable accessibilityRole='button' onPress={()=>{logStartupStage('ROOT_RUNTIME_RETRY',{error_code:failure.code});this.props.onRetry()}} style={{minHeight:52,minWidth:180,borderRadius:18,backgroundColor:'#246bfd',alignItems:'center',justifyContent:'center',paddingHorizontal:20}}><Text style={{color:'#fff',fontSize:18,fontWeight:'800'}}>Повторить</Text></Pressable></View>}
    return this.props.children;
  }
}

export default function App() {
  const[runtimeAttempt,setRuntimeAttempt]=useState(0);
  const LazyAppRuntime=useMemo(createLazyAppRuntime,[runtimeAttempt]);
  useEffect(()=>{logStartupStage('APP_MOUNT')},[]);
  return (
    <SafeAreaProvider>
      <SafeAreaView edges={['top', 'left', 'right']} style={{ flex: 1, backgroundColor: '#F7F7FB' }}>
        <StatusBar barStyle="dark-content" />
        <View style={{flex:1}}>
          <RootErrorBoundary key={runtimeAttempt} onRetry={()=>setRuntimeAttempt(value=>value+1)}><Suspense fallback={<View style={{flex:1,alignItems:'center',justifyContent:'center',padding:24}}><Text style={{fontSize:18,color:'#42475C'}}>Открываем DOME…</Text></View>}><LazyAppRuntime key={runtimeAttempt}/></Suspense></RootErrorBoundary>
          <View pointerEvents='none' accessibilityLabel={BUILD_MARKER} style={{position:'absolute',right:6,bottom:3,zIndex:9999,borderRadius:5,backgroundColor:'rgba(0,0,0,.66)',paddingHorizontal:5,paddingVertical:2}}>
            <Text testID='dome-build-marker' style={{color:'#fff',fontSize:9,fontWeight:'700'}}>{BUILD_MARKER}</Text>
          </View>
        </View>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}
