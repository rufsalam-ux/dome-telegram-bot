export type UiLanguage='ru'|'en'|'de';
export type Screen='auth'|'children'|'add_child'|'hero'|'home'|'lesson'|'lessons'|'movies'|'plans'|'purchase'|'consents'|'admin'|'language'|'parent_verify';
export interface ParentProfile{id:string;name:string;phone?:string;email?:string;country?:string;phoneVerified?:boolean;emailVerified?:boolean;uiLanguage?:UiLanguage;adultAuthorityConfirmed?:boolean}
export interface HeroMetadata{characterBoundingBox:[number,number,number,number];headCenterX:number;headCenterY:number;headBoundingBox?:[number,number,number,number]|null;bodyCenterX:number;bodyCenterY:number;facingDirection:'LEFT'|'RIGHT'|'FRONT'|'UNKNOWN';confidence:number;analysisStatus?:string;analysisVersion?:string;source?:string}
export interface ChildProfile{id:string;parentId:string;name:string;age?:number;learningLanguage?:string;nativeLanguage?:string;languageLevel?:string;workingDifficulty?:number;courseId?:string;activeCharacterId?:string|number|null;heroUrl?:string|null;heroMetadata?:HeroMetadata|null}
export interface ConsentDocument{type:string;version:string;language:UiLanguage;title:string;bodyMarkdown:string;required:boolean;hash:string}
export interface ConsentAcceptance{type:string;version:string;language:UiLanguage;accepted:boolean;hash:string}
export interface PurchaseQuote{planId:string;lessons:number;amountCents:number;currency:string;recurring:boolean;checkoutUrl?:string}
