#!/usr/bin/env python3
"""鲸海数据 API 测试脚本 v3：尝试不同的 App ID / API Key 组合。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ssl, json, urllib.request, urllib.error
from urllib.parse import quote
from config import get_provider

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

TEST_CREDIT_CODE = "91511600MA64WU8N7W"

provider = get_provider("jinghai")
key1 = provider["credentials"]["api_key"]        # jh_567891_8d5ef4e6
key2 = provider.get("credentials", {}).get("api_secret", "")  # jhkey_ed3e53...

print(f"凭证1 (api_key):    {key1}")
print(f"凭证2 (api_secret): {key2}")
print()

# 文档端点: /DataService/api/v3/company/detail/{COMPANY}?queryType=2
base_url = "https://www.kqdaas.com"
endpoint = f"/DataService/api/v3/company/detail/{quote(TEST_CREDIT_CODE)}?queryType=2"
url = f"{base_url}{endpoint}"

# 尝试不同的 header 组合
combos = [
    # (app_id, api_key, desc)
    (key2, key1, "key2=AppID, key1=ApiKey"),     # 长的当 AppID，短的当 ApiKey
    (key1, key2, "key1=AppID, key2=ApiKey"),     # 短的当 AppID，长的当 ApiKey
    ("",     key1, "仅 ApiKey=key1"),              # 只用短 key
    ("",     key2, "仅 ApiKey=key2"),              # 只用长 key
    (key1, key1, "两个都=key1"),                  # 两个都用短 key
    (key2, key2, "两个都=key2"),                  # 两个都用长 key
]

for i, (aid, akey, desc) in enumerate(combos):
    print(f"--- 组合 {i+1}/{len(combos)}: {desc} ---")

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    if aid:
        req.add_header("X-Jinghai-App-Id", aid)
    req.add_header("X-Jinghai-Api-Key", akey)

    try:
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            print(f"  ✅ HTTP {resp.status}")
            print(f"  返回: {json.dumps(data, ensure_ascii=False, indent=2)[:2000]}")
            print("\n🎉 成功！停止尝试。")
            break
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
        except:
            pass
        # 尝试提取 JSON 错误信息
        try:
            err_data = json.loads(err_body)
            print(f"  ❌ HTTP {e.code}: {err_data.get('errmsg', err_body[:100])}")
        except:
            print(f"  ❌ HTTP {e.code}: {err_body[:100]}")
        print()
        continue
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {e}")
        print()
        continue

print("=== 测试结束 ===")
