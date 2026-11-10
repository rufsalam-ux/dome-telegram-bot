import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function fixture({member = {_id:'real-member', status:'APPROVED'}, response = {linked:true}, ok = true} = {}) {
  const calls = [];
  const context = {
    webMethod: (_permissions, fn) => fn, Permissions: {SiteMember:'member'},
    currentMember: {getMember: async options => {assert.deepEqual(JSON.parse(JSON.stringify(options)), {fieldsets:['FULL']}); return member;}},
    secrets: {getSecretValue: async () => ({value:'test-key-' + 'x'.repeat(43)})},
    elevate: fn => fn,
    fetch: async (url, options) => {calls.push({url,options}); return {ok,json:async()=>response};}
  };
  const source = fs.readFileSync(new URL('./classic-schoolTelegram.web.js', import.meta.url),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace(/export const /g,'var ');
  vm.createContext(context); vm.runInContext(source,context);
  return {context,calls};
}
test('member comes from Wix session, not caller input',async()=>{
  const {context,calls}=fixture();
  await context.getTelegramStatus({memberId:'someone-else'});
  assert.equal(JSON.parse(calls[0].options.body).memberId,'real-member');
  assert.equal(calls[0].url,'https://dome-telegram-bot-production-e6f6.up.railway.app/school/telegram/status');
});
test('anonymous or blocked members never call Railway',async()=>{
  for(const member of [null,{_id:'x',status:'BLOCKED'}]){
    const {context,calls}=fixture({member});
    await assert.rejects(()=>context.connectTelegram(),/AUTH_REQUIRED/);
    assert.equal(calls.length,0);
  }
});
test('backend errors and unexpected URLs never escape to clients',async()=>{
  for(const args of [{ok:false},{response:{url:'https://example.com/secret'}}]){
    const {context}=fixture(args);
    assert.equal((await context.connectTelegram()).error,'TELEGRAM_UNAVAILABLE');
  }
});
test('only expected one-time bot URL returned',async()=>{
  const url='https://t.me/BilingvaDom_bot?start=school_'+'a'.repeat(43);
  const {context}=fixture({response:{url,secret:'must not return'}});
  assert.deepEqual(JSON.parse(JSON.stringify(await context.connectTelegram())),{url,expiresIn:600});
});
