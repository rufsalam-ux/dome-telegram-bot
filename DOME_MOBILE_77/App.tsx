import React,{useCallback,useEffect,useRef,useState} from 'react';
import {LogBox,Pressable,ScrollView,StatusBar,Text,View} from 'react-native';
import {SafeAreaProvider,SafeAreaView} from 'react-native-safe-area-context';
import {BootStage,logStartupStage,rootRuntimeFailure,StartupFailure} from './src/engine/startup';
import {AppRuntime} from './src/AppRuntime';

const BUILD_COMMIT=(process.env.EXPO_PUBLIC_BUILD_COMMIT||'unmarked').trim();
const BUILD_TIMESTAMP=(process.env.EXPO_PUBLIC_BUILD_TIMESTAMP||'unknown-time').trim();
export const BUILD_MARKER=`BUILD ${BUILD_COMMIT.slice(0,12)} / ${BUILD_TIMESTAMP}`;

// A tunnel websocket warning is not an application failure. It remains in Metro
// output, but never covers the child-facing UI.
LogBox.ignoreLogs(['Cannot connect to Expo CLI']);
if(!__DEV__)LogBox.ignoreAllLogs(true);

function BootMarker({stage,failure,retryCount}:{stage:BootStage;failure:StartupFailure|null;retryCount:number}){
  return <View pointerEvents='none' accessibilityLabel={BUILD_MARKER} style={{position:'absolute',right:5,bottom:3,zIndex:9999,borderRadius:6,backgroundColor:'rgba(0,0,0,.72)',paddingHorizontal:6,paddingVertical:3,maxWidth:'92%'}}>
    <Text testID='dome-build-marker' style={{color:'#fff',fontSize:9,fontWeight:'800'}}>{BUILD_MARKER}</Text>
    <Text testID='boot-stage-marker' style={{color:'#fff',fontSize:9}}>BOOT STAGE: {stage}</Text>
    <Text testID='boot-error-marker' numberOfLines={2} style={{color:'#fff',fontSize:9}}>BOOT ERROR: {failure?`${failure.code} · ${failure.errorName}: ${failure.errorMessage}`:'NONE'}</Text>
    <Text testID='boot-retry-marker' style={{color:'#fff',fontSize:9}}>RETRY COUNT: {retryCount}</Text>
  </View>;
}

type RootErrorBoundaryProps={
  children:React.ReactNode;
  bootStage:BootStage;
  retryCount:number;
  onRetryPress:()=>void;
  onFailure:(failure:StartupFailure)=>void;
};

class RootErrorBoundary extends React.Component<RootErrorBoundaryProps,{error:Error|null;failure:StartupFailure|null}>{
  state:{error:Error|null;failure:StartupFailure|null}={error:null,failure:null};
  static getDerivedStateFromError(error:Error){return {error,failure:null}}
  componentDidCatch(error:Error,info:React.ErrorInfo){
    const failure=rootRuntimeFailure(error,this.props.bootStage,info.componentStack||'');
    this.setState({failure});this.props.onFailure(failure);
    console.error('DOME_ROOT_RUNTIME_ERROR',JSON.stringify(failure),error,info.componentStack);
    logStartupStage('ROOT_RUNTIME_FAILED',{boot_stage:failure.stage,error_code:failure.code,error_name:failure.errorName,error_message:failure.errorMessage,failing_function:failure.failingFunction,failing_location:failure.failingLocation,stack:failure.stack.slice(0,1600)});
  }
  render(){
    if(this.state.error){
      const failure=this.state.failure||rootRuntimeFailure(this.state.error,this.props.bootStage);
      return <ScrollView testID='fatal-startup-screen' contentContainerStyle={{flexGrow:1,justifyContent:'center',padding:28,backgroundColor:'#F7F7FB'}} keyboardShouldPersistTaps='always'>
        <Text style={{fontSize:34,fontWeight:'900',color:'#20243A'}}>DOME</Text>
        <Text style={{fontSize:17,marginVertical:12,color:'#42475C'}}>{failure.message}</Text>
        <Text testID='fatal-boot-stage' selectable style={{fontSize:12,marginBottom:5,color:'#42475C'}}>BOOT STAGE: {failure.stage}</Text>
        <Text testID='fatal-boot-error' selectable style={{fontSize:12,marginBottom:5,color:'#8A2942'}}>BOOT ERROR: {failure.code} · {failure.errorName}: {failure.errorMessage}</Text>
        <Text selectable style={{fontSize:11,marginBottom:4,color:'#6A7088'}}>FUNCTION: {failure.failingFunction}</Text>
        <Text selectable style={{fontSize:11,marginBottom:8,color:'#6A7088'}}>LOCATION: {failure.failingLocation}</Text>
        <Text testID='fatal-retry-count' style={{fontSize:13,fontWeight:'800',marginBottom:10,color:'#20243A'}}>RETRY COUNT: {this.props.retryCount}</Text>
        <Pressable testID='fatal-retry-button' accessibilityRole='button' disabled={false} collapsable={false} onPress={this.props.onRetryPress} style={({pressed})=>({minHeight:58,borderRadius:18,backgroundColor:pressed?'#174fc2':'#246bfd',alignItems:'center',justifyContent:'center',paddingHorizontal:24})}>
          <Text style={{color:'#fff',fontSize:18,fontWeight:'800'}}>Повторить запуск</Text>
        </Pressable>
        <Text selectable style={{fontSize:9,lineHeight:12,marginTop:12,color:'#6A7088'}}>STACK: {failure.stack||'unavailable'}</Text>
        <Text selectable style={{fontSize:9,marginTop:10,color:'#6A7088'}}>{BUILD_MARKER}</Text>
      </ScrollView>;
    }
    return this.props.children;
  }
}

export default function App(){
  const[runtimeAttempt,setRuntimeAttempt]=useState(0);
  const[retryCount,setRetryCount]=useState(0);
  const[bootStage,setBootStage]=useState<BootStage>('ROOT');
  const[bootFailure,setBootFailure]=useState<StartupFailure|null>(null);
  const retryCountRef=useRef(0);

  const reportBootStage=useCallback((stage:BootStage,failure:StartupFailure|null=null)=>{
    setBootStage(stage);setBootFailure(failure);
    logStartupStage(stage,failure?{error_code:failure.code,error_name:failure.errorName,error_message:failure.errorMessage}:{status:'OK'});
  },[]);
  const registerRetry=useCallback(()=>{
    const next=retryCountRef.current+1;retryCountRef.current=next;setRetryCount(next);
    console.log('RETRY_PRESS_RECEIVED',{retry_count:next,runtime_attempt:runtimeAttempt});
    return next;
  },[runtimeAttempt]);
  const handleRootRetry=useCallback(()=>{
    const next=registerRetry();
    logStartupStage('ROOT_RUNTIME_RETRY',{retry_count:next,runtime_attempt:runtimeAttempt});
    reportBootStage('ROOT');setRuntimeAttempt(value=>value+1);
  },[registerRetry,reportBootStage,runtimeAttempt]);
  const handleRootFailure=useCallback((failure:StartupFailure)=>{setBootStage(failure.stage);setBootFailure(failure)},[]);

  useEffect(()=>{logStartupStage('APP_MOUNT');logStartupStage('APP_RUNTIME_LOADED');reportBootStage('ROOT')},[reportBootStage]);

  return <RootErrorBoundary key={runtimeAttempt} bootStage={bootStage} retryCount={retryCount} onRetryPress={handleRootRetry} onFailure={handleRootFailure}>
    <SafeAreaProvider>
      <SafeAreaView edges={['top','left','right']} style={{flex:1,backgroundColor:'#F7F7FB'}}>
        <StatusBar barStyle='dark-content'/>
        <View style={{flex:1}}>
          <AppRuntime key={runtimeAttempt} onBootStage={reportBootStage} retryCount={retryCount} onRetryReceived={registerRetry}/>
          <BootMarker stage={bootStage} failure={bootFailure} retryCount={retryCount}/>
        </View>
      </SafeAreaView>
    </SafeAreaProvider>
  </RootErrorBoundary>;
}
