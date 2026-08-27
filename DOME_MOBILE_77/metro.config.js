const {getDefaultConfig}=require('expo/metro-config');

const config=getDefaultConfig(__dirname);
const upstreamEnhance=config.server&&config.server.enhanceMiddleware;

config.server={
  ...(config.server||{}),
  enhanceMiddleware(middleware,metroServer){
    const upstream=upstreamEnhance?upstreamEnhance(middleware,metroServer):middleware;
    return (request,response,next)=>{
      if(String(request.url||'').startsWith('/__dome_startup?')){
        try{
          const url=new URL(String(request.url),'http://localhost');
          const stage=String(url.searchParams.get('stage')||'UNKNOWN').slice(0,80);
          const payload=String(url.searchParams.get('payload')||'{}').slice(0,5000);
          console.log(`[DOME_DEVICE_BEACON] ${stage} ${payload}`);
        }catch(error){console.warn('[DOME_DEVICE_BEACON] INVALID',error)}
        response.statusCode=204;response.end();return;
      }
      return upstream(request,response,next);
    };
  },
};

module.exports=config;
