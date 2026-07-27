/**
 * EntHub 快录 - Content Script
 *
 * 在天眼查/企查查/爱企查/风鸟等网站上提取工商信息。
 * 支持两种模式：
 * 1. DOM 结构化提取（优先，精确）
 * 2. 纯文本抓取（fallback，发给 EntHub API 用正则/LLM 提取）
 */

// ── 通用工具 ────────────────────────────────────────────────────────────────

// DOM 提取时需要排除的按钮/操作文字
const NOISE_TEXT = ['复制', '点击复制', '收起', '展开', '更多', '查看详情',
  '复制成功', '已复制', '编辑', '添加', '查看全部', '收起全部',
  '点击', '点击查看', '查看更多', '展开全部', '更多详情', '详情',
  '举报', '纠错', '反馈', '认领', '免费', '下载', '分享'];

function cleanText(text) {
  if (!text) return '';
  let s = text.trim();
  // 移除常见的按钮/操作文字
  for (const noise of NOISE_TEXT) {
    s = s.replace(new RegExp(noise, 'g'), '');
  }
  // 合并多余空白
  s = s.replace(/[\s\u00A0]+/g, ' ').trim();
  // 去掉首尾标点
  s = s.replace(/^[：:，,\s]+|[：:，,\s]+$/g, '');
  return s;
}

// 从单元格元素中取干净文本：先克隆，剥掉按钮/链接/图标等交互元素
function cleanCellText(el) {
  if (!el) return '';
  const clone = el.cloneNode(true);
  // 移除交互/装饰元素
  clone.querySelectorAll(
    'button, a, [class*="copy"], [class*="btn"], [class*="icon"], ' +
    '[class*="more"], [class*="edit"], [class*="action"], ' +
    '[class*="operate"], [class*="toolbar"], svg, img, i'
  ).forEach(e => e.remove());
  return cleanText(clone.textContent);
}

// ── 天眼查 DOM 提取 ─────────────────────────────────────────────────────────

function extractTianyancha() {
  const data = {};

  const nameEl = document.querySelector('.header .name, h1.header-name, [class*="company-name"]');
  if (nameEl) data.name = cleanText(nameEl.textContent);

  const rows = document.querySelectorAll('.detail-table .table-item, .company_info_table tr, .info-table tr');
  rows.forEach(row => {
    const label = row.querySelector('.label, td:first-child, th, dt');
    const value = row.querySelector('.value, td:last-child, dd');
    if (!label || !value) return;

    const key = cleanCellText(label);
    const val = cleanCellText(value);
    if (!key || !val) return;

    if (/统一社会信用代码|信用代码/.test(key)) data.credit_code = val;
    else if (/法定代表人|法人代表/.test(key)) data.legal_person = val;
    else if (/注册资本/.test(key)) data.registered_capital = val;
    else if (/实缴资本/.test(key)) data.paid_capital = val;
    else if (/成立日期|注册日期/.test(key)) data.established_date = val;
    else if (/核准日期/.test(key)) data.approved_date = val;
    else if (/经营状态|登记状态/.test(key)) data.business_status = val;
    else if (/公司类型|企业类型/.test(key)) data.company_type = val;
    else if (/所属行业|行业/.test(key)) data.industry = val;
    else if (/参保人数|社保人数/.test(key)) data.insured_count = val;
    else if (/注册地址|地址/.test(key)) data.address = val;
    else if (/经营范围/.test(key)) data.business_scope = val;
    else if (/营业期限/.test(key)) data.business_term = val;
    else if (/曾用名/.test(key)) data.former_name = val;
    else if (/组织机构代码/.test(key)) data.org_code = val;
    else if (/注册号/.test(key)) data.registration_no = val;
    else if (/电话|联系电话/.test(key) && !data.phone) data.phone = val;
    else if (/邮箱/.test(key) && !data.email) data.email = val;
    else if (/网址|网站/.test(key) && !data.website) data.website = val;
  });

  // 股东信息
  const shareholders = [];
  const shareholderEls = document.querySelectorAll('.table-tbody tr, [class*="shareholder"] tr, .partner-list .item');
  shareholderEls.forEach(row => {
    const nameEl = row.querySelector('td:first-child a, .name, [class*="name"]');
    if (nameEl) {
      const name = cleanText(nameEl.textContent);
      if (name && name.length > 1 && name.length < 30) shareholders.push(name);
    }
  });
  if (shareholders.length > 0) data.shareholders = shareholders.join('; ');

  return data;
}

// ── 企查查 DOM 提取 ─────────────────────────────────────────────────────────

function extractQichacha() {
  const data = {};

  const nameEl = document.querySelector('h1, .company-name, [class*="entName"]');
  if (nameEl) data.name = cleanText(nameEl.textContent);

  const rows = document.querySelectorAll('.list-cell, .table tr, .info-item, [class*="detail"] tr');
  rows.forEach(row => {
    const label = row.querySelector('.label, td:first-child, th, .name, dt');
    const value = row.querySelector('.value, td:last-child, .val, dd');
    if (!label || !value) return;

    const key = cleanCellText(label);
    const val = cleanCellText(value);
    if (!key || !val) return;

    if (/统一社会信用代码/.test(key)) data.credit_code = val;
    else if (/法定代表人/.test(key)) data.legal_person = val;
    else if (/注册资本/.test(key)) data.registered_capital = val;
    else if (/成立日期/.test(key)) data.established_date = val;
    else if (/经营状态/.test(key)) data.business_status = val;
    else if (/公司类型/.test(key)) data.company_type = val;
    else if (/所属行业/.test(key)) data.industry = val;
    else if (/注册地址/.test(key)) data.address = val;
    else if (/经营范围/.test(key)) data.business_scope = val;
  });

  return data;
}

// ── 爱企查 DOM 提取 ─────────────────────────────────────────────────────────

function extractAiqicha() {
  const data = {};

  const nameEl = document.querySelector('h1, .company-name, [class*="title"]');
  if (nameEl) data.name = cleanText(nameEl.textContent);

  const rows = document.querySelectorAll('.detail-item, table tr, .info-row');
  rows.forEach(row => {
    const cells = row.querySelectorAll('td, .label, .value, dt, dd');
    if (cells.length >= 2) {
      const key = cleanCellText(cells[0]);
      const val = cleanCellText(cells[1]);
      if (!key || !val) return;

      if (/统一社会信用代码/.test(key)) data.credit_code = val;
      else if (/法定代表人/.test(key)) data.legal_person = val;
      else if (/注册资本/.test(key)) data.registered_capital = val;
      else if (/成立日期/.test(key)) data.established_date = val;
      else if (/经营状态/.test(key)) data.business_status = val;
      else if (/注册地址/.test(key)) data.address = val;
      else if (/经营范围/.test(key)) data.business_scope = val;
    }
  });

  return data;
}

// ── 风鸟 DOM 提取 ─────────────────────────────────────────────────────────

function extractRiskbird() {
  const data = {};

  // 企业名称
  const nameEl = document.querySelector('h1, .company-name, .ent-name, [class*="companyName"], [class*="ent-name"]');
  if (nameEl) data.name = cleanText(nameEl.textContent);

  // 工商信息表格（风鸟用 key-value 列表或 table）
  const rows = document.querySelectorAll(
    '.info-table tr, .detail-table tr, table tr, .info-item, '
    + '[class*="detail"] tr, [class*="info"] tr, '
    + '.base-info .item, .company-info .item, '
    + 'dl, .kv-row, .field-row'
  );
  rows.forEach(row => {
    const label = row.querySelector('td:first-child, th, .label, dt, .key, [class*="label"]');
    const value = row.querySelector('td:last-child, .value, dd, .val, [class*="value"]');
    if (!label || !value) return;

    const key = cleanCellText(label);
    const val = cleanCellText(value);
    if (!key || !val) return;

    if (/统一社会信用代码|信用代码/.test(key)) data.credit_code = val;
    else if (/法定代表人|法人代表/.test(key)) data.legal_person = val;
    else if (/注册资本/.test(key)) data.registered_capital = val;
    else if (/实缴资本/.test(key)) data.paid_capital = val;
    else if (/成立日期|注册日期/.test(key)) data.established_date = val;
    else if (/核准日期/.test(key)) data.approved_date = val;
    else if (/经营状态|登记状态/.test(key)) data.business_status = val;
    else if (/公司类型|企业类型/.test(key)) data.company_type = val;
    else if (/所属行业|行业/.test(key)) data.industry = val;
    else if (/参保人数|社保人数/.test(key)) data.insured_count = val;
    else if (/注册地址|地址/.test(key)) data.address = val;
    else if (/经营范围/.test(key)) data.business_scope = val;
    else if (/营业期限/.test(key)) data.business_term = val;
    else if (/曾用名/.test(key)) data.former_name = val;
    else if (/组织机构代码/.test(key)) data.org_code = val;
    else if (/注册号/.test(key)) data.registration_no = val;
    else if (/电话|联系电话/.test(key) && !data.phone) data.phone = val;
    else if (/邮箱/.test(key) && !data.email) data.email = val;
    else if (/网址|网站/.test(key) && !data.website) data.website = val;
  });

  // 股东信息
  const shareholders = [];
  const shareholderEls = document.querySelectorAll(
    '.shareholder-list tr, [class*="partner"] tr, [class*="holder"] tr, '
    + '[class*="stockholder"] tr, [class*="investor"] tr'
  );
  shareholderEls.forEach(row => {
    const nameEl = row.querySelector('td:first-child a, .name, [class*="name"]');
    if (nameEl) {
      const name = cleanText(nameEl.textContent);
      if (name && name.length > 1 && name.length < 30) shareholders.push(name);
    }
  });
  if (shareholders.length > 0) data.shareholders = shareholders.join('; ');

  return data;
}

// ── 通用文本提取（fallback）──────────────────────────────────────────────────

function extractPageText() {
  // 移除脚本和样式标签
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script, style, nav, footer, header, iframe, noscript').forEach(el => el.remove());
  return clone.innerText.substring(0, 10000); // 限制长度
}

// ── 主提取逻辑 ──────────────────────────────────────────────────────────────

function extractCompanyInfo() {
  const host = location.hostname;
  let data = {};
  let source = 'text';

  // 根据网站选择提取策略
  if (host.includes('tianyancha.com')) {
    data = extractTianyancha();
    source = 'tianyancha';
  } else if (host.includes('qcc.com')) {
    data = extractQichacha();
    source = 'qcc';
  } else if (host.includes('aiqicha.baidu.com')) {
    data = extractAiqicha();
    source = 'aiqicha';
  } else if (host.includes('riskbird.com')) {
    data = extractRiskbird();
    source = 'riskbird';
  }

  // 如果 DOM 提取结果太少，fallback 到纯文本
  const fieldCount = Object.keys(data).filter(k => k !== 'source').length;
  if (fieldCount < 3) {
    return {
      method: 'text',
      source: host,
      text: extractPageText(),
      fields: data,
    };
  }

  return {
    method: 'dom',
    source: source,
    text: extractPageText(),
    fields: data,
  };
}

// ── 消息监听 ────────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'extract') {
    try {
      const result = extractCompanyInfo();
      sendResponse({ success: true, data: result });
    } catch (e) {
      sendResponse({ success: false, error: e.message });
    }
  }
  return true;
});
