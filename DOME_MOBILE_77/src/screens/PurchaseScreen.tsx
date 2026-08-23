import React,{useCallback,useEffect,useState} from 'react';
import {Alert,Linking,ScrollView} from 'react-native';
import {Body,Button,Card,H1,H2} from '../components/Ui';
import {
  cancelSubscriptionPlanChange,
  confirmSubscriptionPlanChange,
  getSubscription,
  getSubscriptionPlanChangePreview,
} from '../api/mobile';
import {useAppStore} from '../store/AppStore';

type Plan={
  plan_id:string;
  version_id:string;
  title:string;
  lessons_per_week:number;
  price:number;
  currency:string;
  billing_period:string;
};

type Preview={
  current_plan:Plan;
  new_plan:Plan;
  effective_at:string;
  notice:string;
};

const date=(value?:string|null)=>value?new Date(value).toLocaleDateString('ru-RU'):'—';
const money=(value:number|undefined,currency:string|undefined)=>`${Number(value||0).toFixed(2)} ${currency||'EUR'}`;
const periodLabel=(value?:string)=>value==='YEAR'?'год':'месяц';

export function PurchaseScreen(){
  const store=useAppStore();
  const child=store.selectedChild;
  const[data,setData]=useState<any>(null);
  const[preview,setPreview]=useState<Preview|null>(null);
  const[busy,setBusy]=useState(false);

  const load=useCallback(async()=>{
    if(!child)return;
    try{setBusy(true);setData(await getSubscription(child.id,child.courseId||'conversation'))}
    catch(error:any){Alert.alert('Не удалось загрузить тариф',error.message)}
    finally{setBusy(false)}
  },[child?.id,child?.courseId]);

  useEffect(()=>{void load()},[load]);
  if(!child)return null;

  const choose=async(plan:Plan)=>{
    try{
      setBusy(true);
      setPreview(await getSubscriptionPlanChangePreview(child.id,plan.plan_id,plan.billing_period,plan.version_id,child.courseId||'conversation'));
    }catch(error:any){Alert.alert('Не удалось выбрать тариф',error.message)}
    finally{setBusy(false)}
  };

  const confirm=async()=>{
    if(!preview)return;
    try{
      setBusy(true);
      const result=await confirmSubscriptionPlanChange(child.id,preview.new_plan.plan_id,preview.new_plan.billing_period,preview.new_plan.version_id,child.courseId||'conversation');
      setPreview(null);setData((current:any)=>({...current,subscription:result.subscription}));
      if(result.approval_url){
        Alert.alert('Нужно подтверждение PayPal','Войдите в PayPal и подтвердите смену. Без этого текущий тариф останется без изменений.');
        await Linking.openURL(result.approval_url);
      }else Alert.alert('Готово',result.message);
    }catch(error:any){Alert.alert('Не удалось изменить тариф',error.message)}
    finally{setBusy(false)}
  };

  const cancel=async()=>{
    try{
      setBusy(true);
      const result=await cancelSubscriptionPlanChange(child.id,child.courseId||'conversation');
      setPreview(null);setData((current:any)=>({...current,subscription:result.subscription}));
      if(result.approval_url){
        Alert.alert('Нужно подтверждение PayPal',result.message);
        await Linking.openURL(result.approval_url);
      }else Alert.alert('Готово',result.message);
    }catch(error:any){Alert.alert('Не удалось отменить изменение',error.message)}
    finally{setBusy(false)}
  };

  const subscription=data?.subscription;
  const current=subscription?.current_plan as Plan|undefined;
  const pending=subscription?.pending_plan;
  const plans=(data?.plans||[]) as Plan[];

  return <ScrollView contentContainerStyle={{padding:24}}>
    <H1>Мой тариф</H1>
    {busy&&!data?<Card><Body>Загружаем данные с backend…</Body></Card>:null}
    {!busy&&!subscription?<Card><H2>Платная подписка не активна</H2><Body>Бесплатный демонстрационный урок остаётся доступен. Подключение первой оплачиваемой недели выполняется только через подтверждённый payment flow.</Body></Card>:null}
    {subscription&&current?<Card>
      <H2>Текущий тариф: {current.title}</H2>
      <Body>Уроков в неделю: {current.lessons_per_week}</Body>
      <Body>Текущая цена: {money(current.price,current.currency)} за {periodLabel(current.billing_period)}</Body>
      <Body>Следующая дата списания: {date(subscription.next_charge_at)}</Body>
      <Body>Статус подписки: {subscription.status}</Body>
    </Card>:null}

    {pending?<Card>
      <H2>Запланировано изменение</H2>
      <Body>Новый тариф: {pending.lessons_per_week} урок(а) в неделю</Body>
      <Body>Стоимость следующего периода: {money(pending.price,pending.currency)} за {periodLabel(pending.billing_period)}</Body>
      <Body>Начнёт действовать: {date(pending.effective_at)}</Body>
      {pending.provider_status==='PENDING_APPROVAL'?<Body>Ожидается подтверждение изменения в PayPal.</Body>:null}
      {pending.provider_status==='CANCEL_PENDING_APPROVAL'?<Body>Ожидается подтверждение отмены в PayPal.</Body>:null}
      <Button disabled={busy} secondary title='Отменить изменение тарифа' onPress={cancel}/>
    </Card>:null}

    {subscription&&!preview?<>
      <H2>Изменить тариф</H2>
      <Body>Новый тариф начнёт действовать со следующего оплачиваемого периода. До этой даты действует ваш текущий тариф.</Body>
      {plans.map(plan=><Card key={plan.version_id}>
        <H2>{plan.title}</H2>
        <Body>{money(plan.price,plan.currency)} за {periodLabel(plan.billing_period)}</Body>
        <Button disabled={busy||(plan.plan_id===current?.plan_id&&plan.billing_period===current?.billing_period)} secondary={plan.version_id!==pending?.version_id} title={plan.plan_id===current?.plan_id&&plan.billing_period===current?.billing_period?'Текущий тариф':plan.version_id===pending?.version_id?'Запланирован':'Выбрать'} onPress={()=>choose(plan)}/>
      </Card>)}
    </>:null}

    {preview?<Card>
      <H2>Подтверждение изменения</H2>
      <Body>Новый тариф начнёт действовать со следующего оплачиваемого периода. До этой даты действует ваш текущий тариф.</Body>
      <Body>Текущий тариф: {preview.current_plan.title}</Body>
      <Body>Действует до: {date(preview.effective_at)}</Body>
      <Body>Новый тариф: {preview.new_plan.title}</Body>
      <Body>Стоимость следующего периода: {money(preview.new_plan.price,preview.new_plan.currency)} за {periodLabel(preview.new_plan.billing_period)}</Body>
      <Body>Начнет действовать: {date(preview.effective_at)}</Body>
      <Button disabled={busy} title='Подтвердить изменение тарифа' onPress={confirm}/>
      <Button disabled={busy} secondary title='Назад к тарифам' onPress={()=>setPreview(null)}/>
    </Card>:null}

    <Button disabled={busy} title='Назад' secondary onPress={()=>store.setScreen('home')}/>
  </ScrollView>;
}
