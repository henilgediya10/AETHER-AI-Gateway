import os, time, asyncio, random, logging, re, hashlib, json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .router import classify, select_provider
load_dotenv(Path(__file__).resolve().parents[1]/'.env', override=False)
log=logging.getLogger('aether')
logging.basicConfig(level=logging.INFO)
app=FastAPI(title='AETHER AI Gateway')
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],allow_credentials=False,allow_methods=['*'],allow_headers=['*'])
KEYS={'gemini':'GOOGLE_API_KEY','openai':'OPENAI_API_KEY','anthropic':'ANTHROPIC_API_KEY','groq':'GROQ_API_KEY'}
MODELS={'gemini':os.getenv('GEMINI_MODEL','gemini-3.8-flash'),'openai':os.getenv('OPENAI_MODEL','gpt-4o-mini'),'anthropic':os.getenv('ANTHROPIC_MODEL','claude-3-5-haiku-latest'),'groq':os.getenv('GROQ_MODEL','llama-3.3-70b-versatile')}
COOLDOWN={}
CACHE={}
CACHE_TTL=int(os.getenv('CACHE_TTL_SECONDS','300'))
MAX_CONTEXT_TOKENS=int(os.getenv('MAX_CONTEXT_TOKENS','6000'))

def estimate_tokens(text):
    # Fast provider-neutral approximation; authoritative usage comes from provider response.
    return max(1, (len(text)+3)//4)

def cache_key(provider, model, messages):
    payload=json.dumps({'provider':provider,'model':model,'messages':messages},sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
class Message(BaseModel):
    role:Literal['user','assistant']
    content:str=Field(min_length=1,max_length=30000)
class ChatRequest(BaseModel):
    message:str=Field(min_length=1,max_length=30000)
    provider:Literal['auto','gemini','openai','anthropic','groq']='auto'
    history:list[Message]=Field(default_factory=list,max_length=100)
class ProviderError(Exception):
    def __init__(self,provider,status,message,retry_after=0):
        self.provider,self.status,self.message,self.retry_after=provider,status,message,retry_after
        super().__init__(message)
def key_for(provider): return os.getenv(KEYS[provider],'').strip()
def retry_seconds(value):
    if not value:return 0
    try:return max(0,min(120,float(value)))
    except ValueError:
        try:return max(0,min(120,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
        except Exception:return 0
def compact(history,message):
    # Keep newest turns within an approximate token budget; provider usage remains authoritative.
    result=[{'role':m.role,'content':m.content} for m in history]
    result.append({'role':'user','content':message})
    result=result[-24:]
    while len(result)>1 and sum(estimate_tokens(m['content'])+4 for m in result)>MAX_CONTEXT_TOKENS:
        result.pop(0)
    return result[-24:]
async def call_provider(provider,messages,client):
    key=key_for(provider)
    if not key:raise ProviderError(provider,0,'Provider API key is missing')
    model=MODELS[provider]
    headers={'Content-Type':'application/json'}
    if provider=='gemini':
        url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        headers['x-goog-api-key']=key
        body={'contents':[{'role':'model' if m['role']=='assistant' else 'user','parts':[{'text':m['content']}]} for m in messages]}
    elif provider in ('openai','groq'):
        url='https://api.openai.com/v1/chat/completions' if provider=='openai' else 'https://api.groq.com/openai/v1/chat/completions'
        headers['Authorization']='Bearer '+key
        body={'model':model,'messages':messages}
    else:
        url='https://api.anthropic.com/v1/messages'
        headers.update({'x-api-key':key,'anthropic-version':'2023-06-01'})
        body={'model':model,'max_tokens':1024,'messages':messages}
    try: r=await client.post(url,headers=headers,json=body)
    except (httpx.TimeoutException,httpx.TransportError) as e:raise ProviderError(provider,503,type(e).__name__) from None
    if r.status_code>=400:
        try:
            obj=r.json(); err=obj.get('error',obj); detail=err.get('message','Provider request failed') if isinstance(err,dict) else str(err)
        except Exception:detail='Provider returned an invalid error response'
        detail=re.sub(r'AIza[0-9A-Za-z_-]{20,}', '[REDACTED]',str(detail))[:400]
        raise ProviderError(provider,r.status_code,detail,retry_seconds(r.headers.get('Retry-After')))
    try:
        data=r.json()
        if provider=='gemini':
            answer=''.join(part.get('text','') for candidate in data.get('candidates',[]) for part in candidate.get('content',{}).get('parts',[]))
            usage=data.get('usageMetadata',{});tokens={'input':usage.get('promptTokenCount'),'output':usage.get('candidatesTokenCount'),'total':usage.get('totalTokenCount')}
            model=data.get('modelVersion') or model
        elif provider in ('openai','groq'):
            answer=data['choices'][0]['message'].get('content') or ''
            u=data.get('usage',{});tokens={'input':u.get('prompt_tokens'),'output':u.get('completion_tokens'),'total':u.get('total_tokens')}
            model=data.get('model') or model
        else:
            answer=''.join(x.get('text','') for x in data.get('content',[]) if x.get('type')=='text')
            u=data.get('usage',{});tokens={'input':u.get('input_tokens'),'output':u.get('output_tokens'),'total':None}
            model=data.get('model') or model
        if not answer:raise ValueError('Empty model output')
        return {'answer':answer,'model':model,'tokens':tokens}
    except (ValueError,KeyError,IndexError,TypeError) as e:raise ProviderError(provider,502,'Invalid or empty provider response') from e
async def attempt(provider,messages,client):
    if COOLDOWN.get(provider,0)>time.monotonic():raise ProviderError(provider,429,'Provider cooling down',COOLDOWN[provider]-time.monotonic())
    ck=cache_key(provider,MODELS[provider],messages)
    cached=CACHE.get(ck)
    if cached and time.monotonic()-cached['at'] < CACHE_TTL:
        return {**cached['result'],'cached':True}
    CACHE.pop(ck,None)
    for n in range(3):
        try:
            result=await call_provider(provider,messages,client)
            COOLDOWN.pop(provider,None)
            CACHE[ck]={'at':time.monotonic(),'result':result}
            if len(CACHE)>256:
                oldest=min(CACHE,key=lambda k:CACHE[k]['at']); CACHE.pop(oldest,None)
            return {**result,'cached':False}
        except ProviderError as e:
            log.warning('provider=%s status=%s error=%s',provider,e.status,e.message)
            if e.status not in (429,500,502,503,504):raise
            delay=max(e.retry_after,min(12,2**n+random.random()))
            if n==2:
                COOLDOWN[provider]=time.monotonic()+max(15,delay)
                raise
            await asyncio.sleep(delay)
@app.get('/health')
def health():return {'status':'ok'}
@app.get('/providers/status')
def status():return {p:{'configured':bool(key_for(p)),'cooldown_seconds':round(max(0,COOLDOWN.get(p,0)-time.monotonic()))} for p in KEYS}
@app.get('/models')
def models():return MODELS
@app.post('/chat')
async def chat(req:ChatRequest):
    started=time.perf_counter()
    category=classify(req.message)
    selected=select_provider(category,req.provider)
    sequence=[selected]+(['gemini'] if selected!='gemini' else [])
    messages=compact(req.history,req.message)
    failures=[]
    async with httpx.AsyncClient(timeout=httpx.Timeout(30,connect=10)) as client:
        for provider in sequence:
            if not key_for(provider):
                failures.append({'provider':provider,'status':0,'reason':'not configured'});continue
            try:
                result=await attempt(provider,messages,client)
                return {'reply':result['answer'],'provider':provider,'model':result['model'],'category':category,'selected_provider':selected,'fallback':provider!=selected,'tokens':result['tokens'],'cached':result.get('cached',False),'estimated_context_tokens':sum(estimate_tokens(m['content'])+4 for m in messages),'latency_ms':round((time.perf_counter()-started)*1000),'notice':f'Generated by {provider.title()}' if provider!='gemini' else 'Generated by Google Gemini'}
            except ProviderError as e:
                failures.append({'provider':provider,'status':e.status,'reason':e.message})
    raise HTTPException(status_code=503,detail={'message':'No configured provider could complete this request','failures':failures})
