/**
 * EntHub 快录 - Popup Logic
 */

// ── DOM 元素 ────────────────────────────────────────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const connStatus = $('#conn-status');
const connError = $('#conn-error');
const pageHost = $('#page-host');
const pageTitle = $('#page-title');
const btnGrab = $('#btn-grab');
const sectionResult = $('#section-result');
const sectionActions = $('#section-actions');
const extractMeta = $('#extract-meta');
const extractError = $('#extract-error');
const dupWarning = $('#dup-warning');
const fieldsList = $('#fields-list');
const methodSelect = $('#method-select');
const btnSubmit = $('#btn-submit');
const btnOverwrite = $('#btn-overwrite');
const btnRetry = $('#btn-retry');
const overwriteRow = $('#overwrite-row');
const successMsg = $('#success-msg');
const btnSettings = $('#btn-settings');
const mainView = $('#main-view');
const settingsView = $('#settings-view');
const inputUrl = $('#input-url');
const btnSaveSettings = $('#btn-save-settings');
const btnTestConn = $('#btn-test-conn');
const settingsMsg = $('#settings-msg');
const btnBack = $('#btn-back');

// ── 状态 ────────────────────────────────────────────────────────────────────

let currentTab = null;
let grabbedData = null;    // 抓取到的原始数据
let extractedFields = {};  // 提取后的字段
let existingCompany = null;

// ── 初始化 ──────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  // 获取当前标签页信息
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    currentTab = tab;
    pageHost.textContent = new URL(tab.url).hostname;
    pageTitle.textContent = tab.title || '-';
    pageTitle.title = tab.title || '';
  } catch (e) {
    pageHost.textContent = '-';
    pageTitle.textContent = '无法获取页面信息';
  }

  // 测试连接
  testConnection();
});

// ── 连接测试 ────────────────────────────────────────────────────────────────

async function testConnection() {
  connStatus.className = 'status-dot status-unknown';
  try {
    const resp = await sendMessage({ action: 'testConnection' });
    if (resp && resp.ok) {
      connStatus.className = 'status-dot status-ok';
      connStatus.title = '已连接';
      connError.style.display = 'none';
    } else {
      connStatus.className = 'status-dot status-error';
      connStatus.title = '连接失败';
      connError.style.display = 'block';
    }
  } catch (e) {
    connStatus.className = 'status-dot status-error';
    connStatus.title = '连接失败';
    connError.style.display = 'block';
  }
}

// ── 抓取页面 ────────────────────────────────────────────────────────────────

btnGrab.addEventListener('click', async () => {
  if (!currentTab) return;

  btnGrab.disabled = true;
  btnGrab.classList.add('loading');
  btnGrab.textContent = '抓取中...';

  try {
    // 1. 让 content script 提取数据
    const response = await new Promise((resolve, reject) => {
      chrome.tabs.sendMessage(currentTab.id, { action: 'extract' }, (resp) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else {
          resolve(resp);
        }
      });
    });

    if (!response || !response.success) {
      throw new Error(response?.error || '提取失败，请刷新页面后重试');
    }

    grabbedData = response.data;

    // 2. 如果是 DOM 提取，直接展示字段
    if (grabbedData.method === 'dom' && Object.keys(grabbedData.fields).length >= 2) {
      extractedFields = grabbedData.fields;
      renderFields(extractedFields);
      extractMeta.textContent = `${grabbedData.source} · ${Object.keys(extractedFields).length} 个字段`;
      sectionResult.style.display = 'block';
      sectionActions.style.display = 'block';
      btnRetry.style.display = 'none';
    } else {
      // 3. 纯文本模式，调用 EntHub API 提取
      await extractViaAPI(grabbedData.text || '');
    }
  } catch (e) {
    extractError.textContent = e.message;
    extractError.style.display = 'block';
    sectionResult.style.display = 'block';
  } finally {
    btnGrab.disabled = false;
    btnGrab.classList.remove('loading');
    btnGrab.textContent = '📋 抓取页面信息';
  }
});

// ── 调用 EntHub API 提取 ────────────────────────────────────────────────────

async function extractViaAPI(text) {
  const method = methodSelect.value;

  try {
    const resp = await sendMessage({
      action: 'extractText',
      text: text,
      method: method,
    });

    if (!resp || resp.code !== 0) {
      throw new Error(resp?.message || 'API 提取失败');
    }

    const data = resp.data;
    extractedFields = data.fields || {};
    existingCompany = data.existing;

    renderFields(extractedFields);
    extractMeta.textContent = `${data.method_used || method} · ${data.field_count || 0} 个字段`;

    if (data.error) {
      extractError.textContent = data.error;
      extractError.style.display = 'block';
    }

    // 重复检查
    if (existingCompany) {
      dupWarning.innerHTML = `⚠️ 已存在: <strong>${existingCompany.name}</strong> (ID: ${existingCompany.id})`;
      dupWarning.style.display = 'block';
      btnSubmit.style.display = 'none';
      overwriteRow.style.display = 'block';
    } else {
      dupWarning.style.display = 'none';
      btnSubmit.style.display = 'block';
      overwriteRow.style.display = 'none';
    }

    sectionResult.style.display = 'block';
    sectionActions.style.display = 'block';
    btnRetry.style.display = 'block';
  } catch (e) {
    extractError.textContent = e.message;
    extractError.style.display = 'block';
    sectionResult.style.display = 'block';
    sectionActions.style.display = 'block';
    btnRetry.style.display = 'block';
  }
}

// ── 渲染字段列表 ────────────────────────────────────────────────────────────

const FIELD_LABELS = {
  name: '企业名称', credit_code: '信用代码', legal_person: '法定代表人',
  registered_capital: '注册资本', paid_capital: '实缴资本',
  established_date: '成立日期', approved_date: '核准日期',
  business_term: '营业期限', business_status: '经营状态',
  company_type: '公司类型', industry: '所属行业',
  insured_count: '参保人数', province: '省份', city: '城市',
  district: '区县', address: '注册地址', business_scope: '经营范围',
  former_name: '曾用名', website: '网址', email: '邮箱',
  phone: '电话', org_code: '组织机构代码', registration_no: '注册号',
  shareholders: '股东',
};

function renderFields(fields) {
  fieldsList.innerHTML = '';

  const entries = Object.entries(fields).filter(([k]) => k !== 'taxpayer_id');

  if (entries.length === 0) {
    fieldsList.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted);">未提取到字段</div>';
    return;
  }

  for (const [key, value] of entries) {
    const div = document.createElement('div');
    div.className = 'field-item';
    div.innerHTML = `
      <div class="field-label">${FIELD_LABELS[key] || key}</div>
      <div class="field-value">
        <input type="text" value="${escapeHtml(value)}" data-field="${key}">
      </div>
      <button class="field-remove" data-field="${key}" title="移除">✕</button>
    `;
    fieldsList.appendChild(div);
  }

  // 监听输入变化
  fieldsList.querySelectorAll('input').forEach(input => {
    input.addEventListener('change', (e) => {
      extractedFields[e.target.dataset.field] = e.target.value.trim();
    });
  });

  // 监听移除按钮
  fieldsList.querySelectorAll('.field-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const field = e.target.dataset.field;
      delete extractedFields[field];
      e.target.closest('.field-item').remove();
    });
  });
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text || '';
  return div.innerHTML;
}

// ── 提交 ────────────────────────────────────────────────────────────────────

btnSubmit.addEventListener('click', () => doSubmit(false));
btnOverwrite.addEventListener('click', () => doSubmit(true));

async function doSubmit(overwrite) {
  if (!extractedFields.name) {
    alert('缺少企业名称');
    return;
  }

  btnSubmit.disabled = true;
  btnOverwrite.disabled = true;

  try {
    // 收集最新的字段值
    fieldsList.querySelectorAll('input').forEach(input => {
      extractedFields[input.dataset.field] = input.value.trim();
    });

    const text = grabbedData?.text || '';

    const resp = await sendMessage({
      action: 'submit',
      text: text,
      method: methodSelect.value,
      fields: extractedFields,
      overwrite: overwrite,
    });

    if (!resp || resp.code !== 0) {
      throw new Error(resp?.message || '提交失败');
    }

    const data = resp.data;

    if (data.action === 'exists') {
      dupWarning.innerHTML = `⚠️ 已存在: <strong>${data.existing_name}</strong>`;
      dupWarning.style.display = 'block';
      btnSubmit.style.display = 'none';
      overwriteRow.style.display = 'block';
      return;
    }

    // 成功
    const actionText = data.action === 'created' ? '已录入' : '已更新';
    successMsg.innerHTML = `✅ ${actionText}: <strong>${data.name}</strong><br><small>ID: ${data.id}</small>`;
    successMsg.style.display = 'block';
    sectionResult.style.display = 'none';
    sectionActions.style.display = 'none';

    // 在 EntHub 中打开
    const baseUrl = await sendMessage({ action: 'getSettings' });
    if (baseUrl?.enthub_url) {
      setTimeout(() => {
        chrome.tabs.create({ url: `${baseUrl.enthub_url}/company/${data.id}` });
      }, 1500);
    }
  } catch (e) {
    alert(e.message);
  } finally {
    btnSubmit.disabled = false;
    btnOverwrite.disabled = false;
  }
}

// ── 重新提取 ────────────────────────────────────────────────────────────────

btnRetry.addEventListener('click', async () => {
  if (!grabbedData) return;

  const text = grabbedData.text || '';
  if (!text) {
    // 重新抓取
    btnGrab.click();
    return;
  }

  extractError.style.display = 'none';
  dupWarning.style.display = 'none';
  await extractViaAPI(text);
});

// ── 设置面板 ────────────────────────────────────────────────────────────────

btnSettings.addEventListener('click', async () => {
  mainView.style.display = 'none';
  settingsView.style.display = 'block';
  settingsMsg.style.display = 'none';

  const settings = await sendMessage({ action: 'getSettings' });
  inputUrl.value = settings?.enthub_url || 'http://127.0.0.1:5210';
});

btnBack.addEventListener('click', () => {
  settingsView.style.display = 'none';
  mainView.style.display = 'block';
});

btnSaveSettings.addEventListener('click', async () => {
  await sendMessage({
    action: 'saveSettings',
    url: inputUrl.value.trim() || 'http://127.0.0.1:5210',
  });
  settingsMsg.className = 'alert alert-success';
  settingsMsg.textContent = '✅ 已保存';
  settingsMsg.style.display = 'block';
  testConnection();
});

btnTestConn.addEventListener('click', async () => {
  // 先保存
  await sendMessage({
    action: 'saveSettings',
    url: inputUrl.value.trim() || 'http://127.0.0.1:5210',
  });

  const resp = await sendMessage({ action: 'testConnection' });
  if (resp && resp.ok) {
    settingsMsg.className = 'alert alert-success';
    settingsMsg.textContent = `✅ 连接成功 (HTTP ${resp.status})`;
  } else {
    settingsMsg.className = 'alert alert-error';
    settingsMsg.textContent = `❌ 连接失败: ${resp?.error || '未知错误'}`;
  }
  settingsMsg.style.display = 'block';
});

// ── 通信工具 ────────────────────────────────────────────────────────────────

function sendMessage(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (resp) => {
      resolve(resp);
    });
  });
}
