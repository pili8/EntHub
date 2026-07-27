/**
 * EntHub 快录 - Background Service Worker
 *
 * 处理与 EntHub 本地服务的通信。
 */

const DEFAULT_ENTHUB_URL = 'http://127.0.0.1:5210';

// 获取 EntHub 地址
async function getEntHubUrl() {
  const result = await chrome.storage.local.get('enthub_url');
  return result.enthub_url || DEFAULT_ENTHUB_URL;
}

// 调用 EntHub API
async function callEntHubAPI(endpoint, data) {
  const baseUrl = await getEntHubUrl();
  const url = `${baseUrl}${endpoint}`;

  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

  return await resp.json();
}

// 测试连接
async function testConnection() {
  try {
    const baseUrl = await getEntHubUrl();
    const resp = await fetch(`${baseUrl}/quick-import`, {
      method: 'GET',
      signal: AbortSignal.timeout(3000),
    });
    return { ok: resp.ok, status: resp.status };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// 消息处理
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'extract') {
    // 转发给 content script
    chrome.tabs.sendMessage(request.tabId, { action: 'extract' }, (response) => {
      sendResponse(response);
    });
    return true;
  }

  if (request.action === 'testConnection') {
    testConnection().then(sendResponse);
    return true;
  }

  if (request.action === 'extractText') {
    // 调用 EntHub API 提取文本
    callEntHubAPI('/api/quick-import/extract', {
      text: request.text,
      method: request.method || 'auto',
    }).then(sendResponse).catch(e => {
      sendResponse({ code: -1, message: e.message });
    });
    return true;
  }

  if (request.action === 'cleanDom') {
    // 清洗 DOM 提取的原始字段（不重新提取）
    callEntHubAPI('/api/quick-import/clean-dom', {
      fields: request.fields || {},
    }).then(sendResponse).catch(e => {
      sendResponse({ code: -1, message: e.message });
    });
    return true;
  }

  if (request.action === 'submit') {
    // 提交到 EntHub
    callEntHubAPI('/api/quick-import/submit', {
      text: request.text,
      method: request.method || 'auto',
      fields: request.fields,
      overwrite: request.overwrite || false,
    }).then(sendResponse).catch(e => {
      sendResponse({ code: -1, message: e.message });
    });
    return true;
  }

  if (request.action === 'saveSettings') {
    chrome.storage.local.set({
      enthub_url: request.url || DEFAULT_ENTHUB_URL,
    }, () => {
      sendResponse({ ok: true });
    });
    return true;
  }

  if (request.action === 'getSettings') {
    chrome.storage.local.get('enthub_url', (result) => {
      sendResponse({
        enthub_url: result.enthub_url || DEFAULT_ENTHUB_URL,
      });
    });
    return true;
  }
});
