import { LessonComponent } from '../types/lesson';
export interface ValidationIssue{componentId:string;code:string;message:string;}
export function validateComponent(c:LessonComponent):ValidationIssue[]{
 const p:any=c.payload||{}; const out:ValidationIssue[]=[]; const err=(code:string,message:string)=>out.push({componentId:c.id,code,message});
 if(['single_choice','multiple_choice','true_false'].includes(c.type)&&!('correct' in p)) err('MISSING_CORRECT','Choice requires a correct answer.');
 if(c.type==='matching'&&(!Array.isArray(p.pairs)||p.pairs.length<2)) err('MISSING_PAIRS','Matching requires at least 2 explicit pairs.');
 if(c.type==='drag_drop'&&(!Array.isArray(p.items)||!Array.isArray(p.targets)||!p.targets.length)) err('MISSING_TARGETS','Drag/drop requires items and targets.');
 if(['tracing','handwriting'].includes(c.type)&&!p.template) err('MISSING_TEMPLATE','Writing requires an explicit template.');
 if(c.type==='video_pause_question'&&(typeof p.pauseAtSeconds!=='number'||!p.question)) err('VIDEO_PAUSE_INVALID','Video pause question requires pauseAtSeconds and question.');
 if(c.type==='role_reading'&&(!Array.isArray(p.lines)||!p.lines.length)) err('ROLE_LINES_MISSING','Role reading requires ordered lines.');
 return out;
}
