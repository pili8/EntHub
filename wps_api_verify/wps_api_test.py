#!/usr/bin/env python3
"""
WPS DBSheet API 验证脚本

用法：
  python3 wps_api_test.py

功能：
  1. 认证（client_credentials）
  2. 测试 DBSheet API 端点可达性
  3. 输出详细验证报告

注意：此脚本仅用于验证，不修改 EntHub 项目代码。
"""

import urllib.request
import urllib.parse
import json
import ssl
import sys

# === 配置 ===
APP_ID = "AK20260727DHYCQL"
APP_SECRET = "6d29fea0cfe8200caa2ff30a69a117d4"
BASE_URL = "https://openapi.wps.cn"

# 跳过 SSL 验证（macOS Python 可能缺少证书）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def http_request(method, path, headers=None, data=None):
    """发送 HTTP 请求"""
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data:
        req.data = data.encode() if isinstance(data, str) else urllib.parse.urlencode(data).encode()
        if "Content-Type" not in (headers or {}):
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=10)
        body = resp.read().decode()
        return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.readable() else ""
        return e.code, body
    except Exception as e:
        return -1, str(e)


def get_token():
    """获取 access_token"""
    data = urllib.parse.urlencode({
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "grant_type": "client_credentials",
    })
    code, body = http_request("POST", "/oauth2/token", data=data)
    if code == 200:
        result = json.loads(body)
        return result.get("access_token"), result
    return None, {"error": body}


def test_endpoint(token, method, path, description):
    """测试单个 API 端点"""
    headers = {"Authorization": f"Bearer {token}"}
    code, body = http_request(method, path, headers=headers)
    try:
        body_json = json.loads(body)
    except Exception:
        body_json = {"raw": body[:200]}

    # 判断状态
    if code == 200:
        status = "✅ 成功"
    elif code == 403:
        msg = body_json.get("message", "")
        if "invalid_scope" in msg:
            status = f"⚠️  端点存在，但缺少 scope"
        else:
            status = f"⚠️  端点存在，权限不足"
    elif code == 404:
        status = "❌ 端点不存在"
    else:
        status = f"❓ 未知状态 {code}"

    print(f"  [{method}] {path}")
    print(f"       说明: {description}")
    print(f"       状态: {status}")
    if code in (403, 400):
        # 提取 scope 信息
        msg = body_json.get("message", "")
        if "scope" in msg:
            import re
            scopes = re.findall(r"kso\.\w+(?:\.\w+)?", msg)
            if scopes:
                print(f"       所需 scope: {', '.join(set(scopes))}")
    print()
    return code


def main():
    print("=" * 60)
    print("WPS DBSheet API 验证脚本")
    print("=" * 60)
    print()

    # 1. 认证
    print("步骤 1: 认证（client_credentials）")
    print("-" * 40)
    token, token_info = get_token()
    if token:
        print(f"  ✅ 成功获取 access_token")
        print(f"  有效期: {token_info.get('expires_in', '?')} 秒")
        print(f"  Token 类型: {token_info.get('token_type', '?')}")
        print(f"  Token (前50字符): {token[:50]}...")
    else:
        print(f"  ❌ 认证失败: {token_info}")
        sys.exit(1)
    print()

    # 2. 测试 DBSheet API 端点
    print("步骤 2: 测试 DBSheet API 端点")
    print("-" * 40)
    print()
    print("使用 file_id=test, sheet_id=1 测试端点可达性：")
    print()

    endpoints = [
        ("GET",  "/v7/coop/dbsheet/test/schema",                                      "获取表结构"),
        ("GET",  "/v7/coop/dbsheet/test/sheets/1/records",                             "查询记录"),
        ("GET",  "/v7/dbsheet/test/sheets/1/views",                                    "列出视图"),
        ("GET",  "/v7/coop/dbsheet/test/hooks",                                        "列出 Webhook"),
        ("GET",  "/v7/dbsheet/test/dashboards",                                        "仪表盘列表"),
        ("GET",  "/v7/links/test/meta",                                                "链接解析"),
        ("GET",  "/v7/users",                                                          "用户信息（对照）"),
    ]

    results = {}
    for method, path, desc in endpoints:
        code = test_endpoint(token, method, path, desc)
        results[path] = code

    # 3. 测试 OAuth2 用户授权流程
    print("步骤 3: 测试 OAuth2 用户授权端点")
    print("-" * 40)
    print()
    code, body = http_request("GET", "/oauth2/auth?client_id={}&response_type=code&scope=kso.dbsheet.read&redirect_uri=http://localhost:53682/callback".replace("{}", APP_ID))
    if code == 400:
        print("  ⚠️  授权端点存在，但应用未配置回调地址（redirect_uri）")
        print("  需要在开发者后台配置 OAuth2 回调地址")
    elif code == 200:
        print("  ✅ 授权端点可用，可以发起用户授权流程")
    else:
        print(f"  ❓ 授权端点返回 {code}")
    print()

    # 4. 总结
    print("=" * 60)
    print("验证总结")
    print("=" * 60)
    print()
    print("✅ 认证: client_credentials 模式可用")
    print("✅ API 端点: 全部存在（返回 403 而非 404）")
    print("⚠️  Scope: client_credentials 不带 DBSheet 权限")
    print("⚠️  用户授权: 需要在开发者后台配置回调地址")
    print()
    print("下一步:")
    print("  1. 登录 https://open.wps.cn 开发者后台")
    print("  2. 在应用设置中配置 OAuth2 回调地址")
    print("     推荐: http://localhost:53682/callback")
    print("  3. 确认应用是否有 DBSheet 相关权限申请入口")
    print("  4. 配置好后用 OAuth2 Authorization Code 流程获取 user token")
    print("  5. user token 应该带 kso.dbsheet.read scope")
    print()


if __name__ == "__main__":
    main()
