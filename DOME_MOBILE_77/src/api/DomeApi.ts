import { api } from './client';
import { ConsentAcceptance,PurchaseQuote } from '../types/domain';
import { LessonManifest } from '../types/lesson';
export const DomeApi={
 bootstrap:()=>api('/v2/bootstrap'),
 requestPhoneOtp:(phone:string)=>api('/v2/auth/phone/request',{method:'POST',body:JSON.stringify({phone})}),
 verifyPhoneOtp:(phone:string,code:string)=>api('/v2/auth/phone/verify',{method:'POST',body:JSON.stringify({phone,code})}),
 requestEmailOtp:(email:string)=>api('/v2/auth/email/request',{method:'POST',body:JSON.stringify({email})}),
 verifyEmailOtp:(email:string,code:string)=>api('/v2/auth/email/verify',{method:'POST',body:JSON.stringify({email,code})}),
 acceptConsents:(items:ConsentAcceptance[])=>api('/v2/consents/accept',{method:'POST',body:JSON.stringify({items})}),
 quote:(childId:string,planId:string)=>api<PurchaseQuote>('/v2/subscriptions/quote',{method:'POST',body:JSON.stringify({childId,planId})}),
 checkout:(childId:string,planId:string)=>api<{checkoutUrl:string;orderId:string}>('/v2/subscriptions/checkout',{method:'POST',body:JSON.stringify({childId,planId})}),
 listLessons:(childId:string)=>api(`/v2/children/${childId}/lessons`),
 startLesson:(childId:string,lessonId:string)=>api<{manifest:LessonManifest;sessionId:string;attempt:1|2;resumeSceneId?:string}>(`/v2/children/${childId}/lessons/${lessonId}/start`,{method:'POST',body:'{}'}),
 submitScene:(sessionId:string,sceneId:string,result:unknown)=>api(`/v2/lesson-sessions/${sessionId}/scenes/${sceneId}/submit`,{method:'POST',body:JSON.stringify(result)}),
 completeAttempt:(sessionId:string)=>api<{movieId:string;homeworkIssued:boolean}>(`/v2/lesson-sessions/${sessionId}/complete`,{method:'POST',body:'{}'}),
 listMovies:(childId:string)=>api(`/v2/children/${childId}/movies`),
 requestDataExport:(childId:string)=>api(`/v2/children/${childId}/export`,{method:'POST',body:'{}'}),
 requestDeletion:(childId:string,scope:'voice'|'movies'|'child_all')=>api(`/v2/children/${childId}/deletion-requests`,{method:'POST',body:JSON.stringify({scope})}),
 adminDashboard:()=>api('/v2/admin/dashboard'),
 adminEvidence:(parentId:string)=>api(`/v2/admin/parents/${parentId}/evidence-bundle`)
};
