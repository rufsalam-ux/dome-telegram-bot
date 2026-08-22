import React,{useState} from 'react';
import { ScrollView } from 'react-native';
import { Body,Button,Card,H1 } from '../components/Ui';
import { validateComponent } from '../engine/registry';
import { LessonComponent } from '../types/lesson';
const demo:LessonComponent={id:'q1',type:'single_choice',prompt:'Что возьмём в путешествие?',voicePrompt:'Выбери, что возьмём в путешествие.',required:true,canSkip:false,maxAttempts:3,payload:{options:['Шапку','Ласты','Зонт'],correct:0}};
export function LessonPlayerDemo({onExit}:{onExit:()=>void}){const[answer,setAnswer]=useState<number|undefined>();const p=demo.payload as any;const issues=validateComponent(demo);return <ScrollView contentContainerStyle={{padding:24}}><H1>Interactive lesson engine</H1><Card><Body>{demo.prompt}</Body>{p.options.map((x:string,i:number)=><Button key={x} title={(answer===i?'✓ ':'')+x} secondary={answer!==i} onPress={()=>setAnswer(i)}/>)}</Card>{answer!==undefined&&<Body>{answer===p.correct?'Отлично!':'Попробуй ещё раз.'}</Body>}{issues.length>0&&<Body>Validation blocked: {issues[0]?.message}</Body>}<Button title='Выйти и сохранить место' onPress={onExit} secondary/></ScrollView>}
