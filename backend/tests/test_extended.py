import os
os.environ['USE_QWEN']='false'
import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app import main

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    main.COOLDOWN.clear()
    monkeypatch.setenv('GOOGLE_API_KEY','FAKE_TEST_KEY')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)

@pytest.mark.parametrize('status', [400,401,403,404,429,500,502,503])
def test_gemini_http_errors(status):
    transport=httpx.MockTransport(lambda req: httpx.Response(status,json={'error':{'message':'test error'}}))
    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(main.ProviderError) as exc:
                await main.call_provider('gemini',[{'role':'user','content':'hi'}],client)
            assert exc.value.status==status
    asyncio.run(go())

def test_gemini_success_and_usage():
    def handler(req):
        assert req.headers['x-goog-api-key']=='FAKE_TEST_KEY'
        assert req.url.path.endswith(':generateContent')
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'It works'}]}}],'usageMetadata':{'promptTokenCount':3,'candidatesTokenCount':2,'totalTokenCount':5}})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r=await main.call_provider('gemini',[{'role':'user','content':'hi'}],client)
            assert r['answer']=='It works' and r['tokens']['total']==5
    asyncio.run(go())

def test_timeout():
    def handler(req): raise httpx.ConnectTimeout('timed out')
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(main.ProviderError) as exc:
                await main.call_provider('gemini',[{'role':'user','content':'hi'}],client)
            assert exc.value.status==503
    asyncio.run(go())

def test_cooldown_no_calls():
    main.COOLDOWN['gemini']=main.time.monotonic()+30
    async def go():
        with patch('app.main.call_provider',new_callable=AsyncMock) as fn:
            with pytest.raises(main.ProviderError) as exc:
                await main.attempt('gemini',[{'role':'user','content':'hi'}],None)
            assert exc.value.status==429
            fn.assert_not_called()
    asyncio.run(go())

def test_fallback_after_other_provider_failure(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','FAKE_TEST_KEY')
    async def fake(provider,messages,client):
        if provider=='openai':raise main.ProviderError('openai',503,'outage')
        return {'answer':'Gemini response','model':'test-model','tokens':{'input':1,'output':2,'total':3}}
    with patch('app.main.call_provider',side_effect=fake),patch('app.main.asyncio.sleep',new_callable=AsyncMock):
        r=TestClient(main.app).post('/chat',json={'message':'write python code','provider':'openai'})
    assert r.status_code==200 and r.json()['provider']=='gemini'

def test_404_no_retry():
    async def go():
        with patch('app.main.call_provider',new_callable=AsyncMock,side_effect=main.ProviderError('gemini',404,'not found')) as fn:
            with pytest.raises(main.ProviderError):await main.attempt('gemini',[],None)
            assert fn.await_count==1
    asyncio.run(go())

def test_no_secret_in_zip():
    from pathlib import Path
    assert not (Path(__file__).resolve().parents[1]/'.env').exists()
