export interface EvidenceContext {
  eventId:string; parentId:string; childId?:string; occurredAtUtc:string;
  ipHash?:string; sessionId:string; deviceInstallationId?:string; appVersion:string;
}
export interface ConsentEvidence extends EvidenceContext {
  kind:'consent'; consentType:string; documentVersion:string; documentHash:string;
  language:string; phoneVerified:boolean; emailVerified:boolean;
}
export interface PurchaseEvidence extends EvidenceContext {
  kind:'purchase'; orderId:string; provider:string; providerTransactionId?:string;
  planId:string; lessons:number; accessMonths:number; amountCents:number; currency:string;
  recurring:boolean; immediateDigitalAccessConfirmed:boolean;
}
export interface LessonEvidence extends EvidenceContext {
  kind:'lesson'; lessonId:string; attempt:1|2; event:'started'|'resumed'|'completed';
  completedScenes?:number; movieId?:string; homeworkId?:string;
}
export type EvidenceEvent=ConsentEvidence|PurchaseEvidence|LessonEvidence;
