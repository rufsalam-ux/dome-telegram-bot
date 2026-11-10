import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function fixture() {
  const elements = Object.fromEntries(['connect','refresh','disconnect','linkBox','botLink','status','notice'].map(id => [id, {hidden:false,disabled:false,textContent:'',removeAttribute(name){delete this[name];}}]));
  const sent = [], timers = new Map(); let listener, id = 0;
  const parent = {postMessage: data => sent.push(data)};
  const context = {document:{getElementById:key=>elements[key]},parent,
    window:{addEventListener:(_,fn)=>listener=fn},confirm:()=>true,
    setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id;},clearTimeout:key=>timers.delete(key)};
  const source = fs.readFileSync(new URL('./classic-telegram.html',import.meta.url),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(source,context);
  const receive = (data,source=parent)=>listener({source,data:{channel:'school-telegram-v1',...data}});
  return {elements,sent,timers,receive};
}
test('untrusted iframe messages cannot change account status',()=>{
  const f=fixture(); f.receive({type:'result',action:'status',linked:true},{});
  assert.equal(f.elements.status.textContent,'');
  assert.equal(f.elements.connect.disabled,true);
});
test('duplicate clicks wait for reply and disconnect requires a separate action',()=>{
  const f=fixture(); f.receive({type:'result',action:'status',linked:false});
  f.elements.connect.onclick(); f.elements.connect.onclick();
  assert.deepEqual(f.sent.map(x=>x.action),['status','connect']);
  f.receive({type:'result',action:'status',linked:true});
  assert.equal(f.elements.connect.hidden,true);
  assert.equal(f.elements.disconnect.hidden,false);
  f.elements.disconnect.onclick();
  assert.equal(f.sent.at(-1).action,'disconnect');
  f.receive({type:'result',action:'disconnect',linked:false});
  assert.equal(f.elements.disconnect.hidden,true);
});
test('only the expected bot URL is exposed and expired links disappear',()=>{
  const f=fixture();f.receive({type:'result',action:'connect',url:'https://evil.example/'});
  assert.equal(f.elements.botLink.href,undefined);
  const url='https://t.me/BilingvaDom_bot?start=school_'+'a'.repeat(43);
  f.receive({type:'result',action:'connect',url});
  assert.equal(f.elements.botLink.href,url);
  [...f.timers.values()].find(x=>x.delay===600000).fn();
  assert.equal(f.elements.botLink.href,undefined);
  assert.equal(f.elements.linkBox.hidden,true);
});
test('service failure removes any previous connection link',()=>{
  const f=fixture();f.receive({type:'result',action:'connect',url:'https://t.me/BilingvaDom_bot?start=school_'+'a'.repeat(43)});
  f.receive({type:'error',message:'Unavailable'});
  assert.equal(f.elements.botLink.href,undefined);
  assert.equal(f.elements.notice.textContent,'Unavailable');
  assert.equal(f.elements.refresh.disabled,false);
});
