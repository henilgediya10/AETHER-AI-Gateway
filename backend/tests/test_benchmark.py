"""Offline regression/quality smoke tests; not a substitute for model evaluation."""
import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from app import main
from app.router import classify

CASES=[
 ('Write a python function','coding'),('debug this traceback','coding'),('javascript code','coding'),
 ('Solve an algebra equation','mathematics'),('calculate the area','mathematics'),('find the integral','mathematics'),
 ('Analyze system architecture','complex analysis'),('compare in depth','complex analysis'),
 ('prove a logic puzzle','reasoning'),('reason about this puzzle','reasoning'),
 ('What is the capital of Japan?','general'),('Hello there','general')]
@pytest.mark.parametrize('prompt,expected',CASES)
def test_classification_cases(prompt,expected):assert classify(prompt)==expected
@pytest.mark.parametrize('provider', ['gemini','openai','anthropic','groq'])
def test_provider_request_format(provider,monkeypatch):
    monkeypatch.setenv(main.KEYS[provider],'FAKE_TEST_KEY')
    def handle(req):
        assert req.method=='POST'
        assert ('FAKE_TEST_KEY' in req.headers.get('authorization','')) or (req.headers.get('x-goog-api-key')=='FAKE_TEST_KEY') or (req.headers.get('x-api-key')=='FAKE_TEST_KEY')
        if provider=='gemini':return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':'ok'}]}}]})
        if provider=='anthropic':return httpx.Response(200,json={'content':[{'type':'text','text':'ok'}]})
        return httpx.Response(200,json={'choices':[{'message':{'content':'ok'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result=await main.call_provider(provider,[{'role':'user','content':'hi'}],client)
            assert result['answer']=='ok'
    asyncio.run(run())
@pytest.mark.parametrize('size',range(1,101))
def test_context_budget_and_last_message(size):
    history=[main.Message(role='user',content='a'*400) for _ in range(size)]
    result=main.compact(history,'latest request')
    assert result[-1]['content']=='latest request'
    assert len(result)<=24
    assert sum(len(m['content']) for m in result)<=24000
@pytest.mark.parametrize('code', [400,401,403,404,408,409,422])
def test_permanent_errors_do_not_retry(code):
    async def run():
        with patch('app.main.call_provider',new_callable=AsyncMock,side_effect=main.ProviderError('gemini',code,'error')) as fn:
            with pytest.raises(main.ProviderError):await main.attempt('gemini',[],None)
            assert fn.await_count==1
    main.COOLDOWN.clear();asyncio.run(run())
