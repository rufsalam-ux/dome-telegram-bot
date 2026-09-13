import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.db.models import Base,Parent,Child,Character
from app.core.config import settings
from app.services.mobile_tokens import issue_session_token
from app.webapp import mobile_api

@pytest.mark.asyncio
async def test_preset_reselection_retains_identity_and_profile(monkeypatch,tmp_path):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'db.sqlite'))
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(mobile_api,'SessionLocal',sessions)
    monkeypatch.setattr(settings,'mobile_auth_secret','test-hero-secret-longer-than-thirty-two-characters')
    monkeypatch.setattr(mobile_api,'preset_character_path',lambda catalog:tmp_path/(catalog+'.png'))
    monkeypatch.setattr(mobile_api,'preset_character_geometry',lambda catalog:{'source':'preset_catalog'})
    async with sessions() as db:
        parent=Parent(display_name='QA',email='qa@example.invalid',email_verified=True)
        db.add(parent);await db.flush()
        child=Child(parent_id=parent.id,display_name='QA child',target_language='ru',native_language='en')
        db.add(child);await db.commit();cid=child.id;pid=parent.id
    app=web.Application();app.router.add_post('/api/mobile/child/{child_id}/hero/preset',mobile_api.hero_preset)
    client=TestClient(TestServer(app));await client.start_server()
    try:
        headers={'Authorization':'Bearer '+issue_session_token(pid)}
        ids=[]
        for catalog in ['cat','cat','robot','cat']:
            response=await client.post(f'/api/mobile/child/{cid}/hero/preset',json={'catalog_id':catalog},headers=headers)
            assert response.status==200,await response.text()
            ids.append((await response.json())['character_id'])
        assert ids[0]==ids[1]==ids[3] and ids[2]!=ids[0]
        async with sessions() as db:
            assert (await db.get(Child,cid)).active_character_id==ids[0]
            assert await db.scalar(select(func.count()).select_from(Character))==2
    finally:
        await client.close();await engine.dispose()
