import os
os.environ['USE_QWEN']='false'
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock,patch
from app.main import app,ProviderError,COOLDOWN,compact
from app.router import classify,select_provider
client=TestClient(app)
def test_health():assert client.get('/health').json()['status']=='ok'
def test_status_no_keys():assert 'gemini' in client.get('/providers/status').json()
def test_models():assert 'gemini' in client.get('/models').json()
def test_classification():assert classify('debug my python code')=='coding'
def test_routing():assert select_provider('coding')=='openai'
def test_context():assert compact([], 'hello')[-1]['content']=='hello'
def test_missing_keys(monkeypatch):
    for k in ['GOOGLE_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY','GROQ_API_KEY']:monkeypatch.delenv(k,raising=False)
    r=client.post('/chat',json={'message':'hello','provider':'openai'})
    assert r.status_code==503
    assert r.json()['detail']['failures'][0]['reason']=='not configured'
def test_gemini_fallback(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setenv('GOOGLE_API_KEY','test-only')
    COOLDOWN.clear()
    result={'answer':'Hello','model':'gemini-test','tokens':{'input':1,'output':1,'total':2}}
    with patch('app.main.call_provider',new_callable=AsyncMock,return_value=result):
        r=client.post('/chat',json={'message':'python code','provider':'openai'})
    assert r.status_code==200 and r.json()['provider']=='gemini' and r.json()['fallback']
def test_provider_error_404(monkeypatch):
    monkeypatch.setenv('GOOGLE_API_KEY','test-only')
    COOLDOWN.clear()
    with patch('app.main.call_provider',new_callable=AsyncMock,side_effect=ProviderError('gemini',404,'model missing')):
        r=client.post('/chat',json={'message':'hello','provider':'gemini'})
    assert r.status_code==503 and r.json()['detail']['failures'][0]['status']==404
