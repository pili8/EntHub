#!/usr/bin/env python3
"""探测鲸海数据联系方式 API 端点。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssl, json, urllib.request, urllib.error
from urllib.parse import quote
from config import get_provider

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

p = get_provider('jinghai')
app_id = p['credentials']['app_id']
api_key = p['credentials']['api_key']
cc = '91511600MA64WU8N7W'

paths = [
    '/DataService/api/v3/company/contact',
    '/DataService/api/v3/company/phone',
    '/DataService/api/v3/company/telephone',
]
for path in paths:
    url = f'https://www.kqdaas.com{path}/{quote(cc)}?queryType=2'
    req = urllib.request.Request(url)
    req.add_header('Accept', 'application/json')
    req.add_header('X-Jinghai-App-Id', app_id)
    req.add_header('X-Jinghai-Api-Key', api_key)
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            body = resp.read().decode('utf-8')
            print(f'{path}: HTTP {resp.status}')
            print(f'  body: {body[:500]}')
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8','replace')[:300]
        try: msg = json.loads(err).get('errmsg','')
        except: msg = err[:150]
        print(f'{path}: HTTP {e.code} -> {msg}')
    except Exception as e:
        print(f'{path}: ERROR {e}')
