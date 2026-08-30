export const MOVIE_ACTIVE_STATES=new Set(['QUEUED','RUNNING','PROCESSING']);
export const MOVIE_SUCCESS_STATES=new Set(['SUCCEEDED','READY']);
export const MOVIE_RETRY_STATES=new Set(['FAILED','TIMED_OUT']);

export type MovieIdentity={
  session_id:number;
  run_id:number|null;
  run_number:number|null;
  job_id:string|null;
  attempt_id:string|null;
  movie_url:string|null;
};

export type NormalizedMovieState=MovieIdentity&{
  status:string;
  stage:string|null;
  progress:number;
  error_code:string|null;
  error_message:string|null;
  can_retry:boolean;
};

function optionalNumber(value:unknown):number|null{
  const number=Number(value);
  return value!==null&&value!==undefined&&value!==''&&Number.isFinite(number)?number:null;
}

export function normalizeMovieState(payload:any,sessionId?:number):NormalizedMovieState{
  const status=String(payload?.status||payload?.movie_status||'NOT_CREATED').toUpperCase();
  const runNumber=optionalNumber(payload?.run_id??payload?.run_number);
  return {
    session_id:Number(payload?.session_id??sessionId??0),
    run_id:runNumber,
    run_number:runNumber,
    job_id:String(payload?.job_id||payload?.movie_job_id||'').trim()||null,
    attempt_id:String(payload?.attempt_id||payload?.movie_attempt_id||'').trim()||null,
    movie_url:String(payload?.url||payload?.movie_url||'').trim()||null,
    status,
    stage:String(payload?.stage||payload?.movie_stage||'').trim()||null,
    progress:Math.max(0,Math.min(100,Number(payload?.progress??payload?.movie_progress??0)||0)),
    error_code:String(payload?.error_code||payload?.movie_error_code||'').trim()||null,
    error_message:String(payload?.error_message||payload?.movie_error_message||payload?.error||'').trim()||null,
    can_retry:MOVIE_RETRY_STATES.has(status)&&Boolean(payload?.can_retry!==false),
  };
}

export function movieIdentity(movie:Partial<NormalizedMovieState>):MovieIdentity{
  return {
    session_id:Number(movie.session_id||0),run_id:movie.run_id??movie.run_number??null,
    run_number:movie.run_number??movie.run_id??null,job_id:movie.job_id||null,
    attempt_id:movie.attempt_id||null,movie_url:movie.movie_url||null,
  };
}

export function completedMoviePayload(payload:any,sessionId:number):any{
  const movie=normalizeMovieState(payload,sessionId);
  return {...payload,session_id:movie.session_id,run_id:movie.run_id,run_number:movie.run_number,
    movie_status:movie.status,movie_stage:movie.stage,movie_progress:movie.progress,
    movie_job_id:movie.job_id,movie_attempt_id:movie.attempt_id,movie_url:movie.movie_url,
    movie_error_code:movie.error_code,movie_error_message:movie.error_message,can_retry:movie.can_retry};
}
