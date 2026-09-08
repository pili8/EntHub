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

  // 企业名称：风鸟用 h1 或特定 class
  let nameEl = document.querySelector(
    'h1, .company-name, .ent-name, [class*="companyName"], [class*="ent-name"], ' +
    '.name-text, .header-title, .ent-name-text, [class*="entName"]'
  );
  if (nameEl) {
    data.name = cleanCellText(nameEl);
  }

  // 如果没找到，用强力方案：从页面顶部查找包含“公司”后缀的文本
  if (!data.name) {
    // 找所有文本节点中包含“公司”的，取最短的那个（公司名通常是最短的）
    const walker = document.createTreeWalker(
      document.body, NodeFilter.SHOW_TEXT, null
    );
    let bestName = '';
    let bestLen = 999;
    while (walker.nextNode()) {
      const t = (walker.currentNode.textContent || '').trim();
      // 跳过太长的（超过30字不是公司名）和太短的（少于4字）
      if (t.length < 4 || t.length > 30) continue;
      // 必须包含公司后缀
      if (!/(有限公司|股份有限公司|有限责任公司|合伙企业|集团)$/.test(t) &&
          !/(有限公司|股份有限公司|有限责任公司|合伙企业|集团)/.test(t)) continue;
      // 排除包含 UI 文字的
      if (/(复制|点击|查看|更多|展开|收起|关联|投资|法定|经营|注册|成立|核准|营业|参保|地址|电话|邮箱|网址|股东|变更|分支)/.test(t)) continue;
      if (t.length < bestLen) {
        bestLen = t.length;
        bestName = t;
      }
    }
    if (bestName) data.name = bestName;
  }

  // 工商信息表格：风鸟用 key-value 列表或 table
  // 扩大选择器范围，适配风鸟的实际 DOM 结构
  const rows = document.querySelectorAll(
    '.info-table tr, .detail-table tr, table tr, .info-item, '
    + '[class*="detail"] tr, [class*="info"] tr, '
    + '.base-info .item, .company-info .item, '
    + 'dl, .kv-row, .field-row, '
    + '[class*="basic"] [class*="item"], [class*="entInfo"] [class*="item"], '
    + '.ent-info .item, .base-info-item, .info-row, .info-line, '
    + '[class*="infoItem"], [class*="infoRow"], [class*="infoLine"], '
    + '[class*="kvItem"], [class*="detailItem"]'
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
    else if (/电话|联系电话/.test(key) && !data.phone) {
      // 电话字段：先用 textContent 深度抓取（不经过 cleanCellText，不删子元素）
      const rawPhoneText = value.textContent || val;
      const directPhones = rawPhoneText.match(/1[3-9]\d{9}/g);
      if (directPhones) {
        const valid = directPhones.filter(_isPhone);
        if (valid.length > 0) data.phone = valid.join('; ');
      }
      if (!data.phone) data.phone = val;
    }
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

  // 1. 移除脚本、样式、导航等无关元素
  clone.querySelectorAll(
    'script, style, nav, footer, header, iframe, noscript'
  ).forEach(el => el.remove());

  // 2. 移除按钮、复制图标、操作栏等 UI 元素（这是脏数据的根源）
  clone.querySelectorAll(
    'button, [class*="copy"], [class*="btn"], [class*="icon"], ' +
    '[class*="more"], [class*="edit"], [class*="action"], ' +
    '[class*="operate"], [class*="toolbar"], [class*="tooltip"], ' +
    '[class*="popup"], [class*="modal"], [class*="drawer"], ' +
    '[class*="loading"], [class*="skeleton"], ' +
    'svg, img, i, span[class*="arrow"], [class*="close"]'
  ).forEach(el => el.remove());

  // 3. 清除隐藏元素样式，确保 innerText 能抓到隐藏在 display:none 里的电话
  clone.querySelectorAll('*').forEach(el => {
    const style = el.style;
    if (style.display === 'none') style.display = '';
    if (style.visibility === 'hidden') style.visibility = '';
    if (style.opacity === '0') style.opacity = '';
  });

  // 4. 取 innerText 并做文本级清洗
  let text = clone.innerText.substring(0, 10000);

  // 5. 文本级去噪：移除残留的 UI 文字
  const UI_NOISE = [
    '点击复制', '复制成功', '已复制', '点击查看', '查看更多',
    '展开全部', '收起全部', '展开', '收起', '更多详情',
    '查看详情', '全屏查看', '查看全部', '免费', '下载', '分享',
    '举报', '纠错', '反馈', '认领', '编辑', '添加', '导出',
    '复制', '点击', '详情', '更多',
  ];
  for (const noise of UI_NOISE) {
    // 用全局替换，注意转义
    text = text.split(noise).join('');
  }
  // 合并多余空白
  text = text.replace(/[\s\u00A0]+/g, ' ').trim();

  return text;
}

// ── 插件自带正则解析（不依赖 EntHub 后端）─────────────────────────────────
// 核心思路：先按字段标签把文本切成段，每段只含一个字段的值
// 然后对每段做通用清洗（去掉"复制""关联N家企业"等噪音词），再提取具体值

// 所有已知字段标签（用于切分文本）
// 标签后面可能跟冒号、空格、换行，也可能直接接值
const FIELD_LABELS_KNOWN = [
  '企业名称', '公司名称', '单位名称', '曾用名', '历史名称', '前身',
  '法定代表人', '法人代表', '负责人',
  '经营状态', '登记状态', '存续状态', '企业状态',
  '注册资本', '注册资金', '实缴资本', '实缴资金',
  '成立日期', '注册日期', '成立时间', '核准日期', '核准时间',
  '营业期限', '经营期限', '公司类型', '企业类型', '企业(机构)类型',
  '所属行业', '国标行业', '行业分类',
  '所属地区', '参保人数', '社保人数', '参加社保人数',
  '注册地址', '企业地址', '公司地址', '通信地址', '通讯地址',
  '经营范围', '经营业务范围',
  '统一社会信用代码', '信用代码', '统一信用代码',
  '组织机构代码', '注册号', '执照编号', '工商注册号',
  '纳税人识别号', '纳税人编号',
  '英文名', '英文名称', '英文名字', '登记机关', '核准机关', '主管部门',
  '电话', '联系电话', '邮箱', '电子邮箱', '电子邮件',
  '网址', '网站', '官网',
  '股东信息', '股东', '主要人员', '对外投资', '变更记录', '变更信息',
  '企业年报', '实际控制人', '受益所有人', '受益股东', '高级职员',
  '同业分析', '关联方', '关联企业', '分支机构', '控制企业',
  '财务数据', '疑似实际', '最终受益',
];

// 通用噪音词（跟工商信息无关，出现在值里要删掉）
const NOISE_WORDS = [
  '复制', '点击复制', '复制成功', '已复制',
  '点击查看', '查看更多', '展开全部', '收起全部', '展开', '收起',
  '查看详情', '查看全部', '更多详情', '更多', '详情',
  '免费', '下载', '分享', '举报', '纠错', '反馈', '认领',
  '编辑', '添加', '导出', '监控', '收藏', '查询',
  '关联', '家企业',  // "关联3家企业"会拆成"关联"+"家企业"
  '简介', '查看', '全部',
];

// 把文本按字段标签切分成 {标签: 值} 的映射
function _splitByLabels(text) {
  const segments = {};
  // 构造一个大的正则：匹配任意已知标签 + 可能的冒号/空格 + 到下一个标签前的值
  // 标签可能跟冒号(中文/英文)、空格、换行
  const labelPattern = FIELD_LABELS_KNOWN
    .map(l => l.replace(/[()]/g, '\\$&'))  // 转义括号
    .join('|');
  // 匹配：标签 + [：:\s]* + 值(到下一个标签或行尾)
  const re = new RegExp(
    '(' + labelPattern + ')([：:\\s]*)',
    'g'
  );

  // 找到所有标签的位置
  const matches = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    matches.push({
      label: m[1],
      pos: m.index,
      valueStart: m.index + m[0].length,
    });
  }

  for (let i = 0; i < matches.length; i++) {
    const valueEnd = (i + 1 < matches.length)
      ? matches[i + 1].pos
      : text.length;
    let value = text.substring(matches[i].valueStart, valueEnd);
    // 通用清洗：去掉噪音词
    value = _cleanSegment(value);
    if (value) {
      // 标签名归一化（多个别名只保留第一个出现的）
      const key = _normalizeLabel(matches[i].label);
      if (key && !segments[key]) {
        segments[key] = value;
      }
    }
  }

  return segments;
}

// 噪音词清洗：去掉跟工商信息无关的文字
function _cleanSegment(value) {
  if (!value) return '';
  let s = value.trim();
  // 去掉所有噪音词
  for (const noise of NOISE_WORDS) {
    // 用 split + join 全局替换
    s = s.split(noise).join('');
  }
  // 去掉 "关联3家企业" 残留的数字+家
  s = s.replace(/\d+家$/g, '');
  // 去掉连续的空白和标点
  s = s.replace(/[\s\u00A0]+/g, ' ').trim();
  s = s.replace(/^[，,；;：:、\s]+|[，,；;：:、\s]+$/g, '');
  return s;
}

// 判断一个数字串是否真的是电话号码（排除账号ID、订单号、日期数字等）
function _isPhone(s) {
  if (!s) return false;
  const digits = s.replace(/\D/g, '');
  const len = digits.length;
  // 长度必须在 7-12 之间
  if (len < 7 || len > 12) return false;

  // 手机号：1开头+3-9第二位+11位
  if (len === 11 && /^1[3-9]\d{9}$/.test(digits)) return true;

  // 带分隔符的固话（如 0817-2449229、028-12345678）优先判断
  // 避免被纯数字分支的 len===11 规则误拒
  if (s.includes('-') || s.includes(' ')) {
    // 排除日期格式
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return false;
    const parts = s.split(/[-\s]/);
    if (parts.length >= 2 && parts[0].startsWith('0') && parts[0].length >= 3) {
      // 号码部分必须是7-8位
      const numPart = parts.slice(1).join('');
      if (numPart.length >= 7 && numPart.length <= 8) return true;
    }
  }

  // 纯数字固话：0开头，总长10或12位（排除11位，很可能是订单号）
  if (digits[0] === '0' && (len === 10 || len === 12)) {
    // 排除以"20"开头的（日期/订单号格式如2024072931）
    if (/^20/.test(digits)) return false;
    // 排除连续递增/递减的数字
    if (/^(?:0123|1234|2345|3456|4567|5678|6789|9876|8765|7654|6543|5432|4321|3210)/.test(digits)) return false;
    // 排除全同号
    if (/^(\d)\1+$/.test(digits)) return false;
    return true;
  }

  return false;
}

// 标签名归一化：把"法定代表人""法人代表"都映射到 legal_person
function _normalizeLabel(label) {
  const map = {
    '企业名称': 'name', '公司名称': 'name', '单位名称': 'name',
    '曾用名': 'former_name', '历史名称': 'former_name', '前身': 'former_name',
    '法定代表人': 'legal_person', '法人代表': 'legal_person', '负责人': 'legal_person',
    '经营状态': 'business_status', '登记状态': 'business_status',
    '存续状态': 'business_status', '企业状态': 'business_status',
    '注册资本': 'registered_capital', '注册资金': 'registered_capital',
    '实缴资本': 'paid_capital', '实缴资金': 'paid_capital',
    '成立日期': 'established_date', '注册日期': 'established_date', '成立时间': 'established_date',
    '核准日期': 'approved_date', '核准时间': 'approved_date',
    '营业期限': 'business_term', '经营期限': 'business_term',
    '公司类型': 'company_type', '企业类型': 'company_type', '企业(机构)类型': 'company_type',
    '所属行业': 'industry', '国标行业': 'industry', '行业分类': 'industry',
    '参保人数': 'insured_count', '社保人数': 'insured_count', '参加社保人数': 'insured_count',
    '注册地址': 'address', '企业地址': 'address', '公司地址': 'address',
    '通信地址': 'mailing_address', '通讯地址': 'mailing_address',
    '经营范围': 'business_scope', '经营业务范围': 'business_scope',
    '统一社会信用代码': 'credit_code', '信用代码': 'credit_code', '统一信用代码': 'credit_code',
    '组织机构代码': 'org_code',
    '注册号': 'registration_no', '执照编号': 'registration_no', '工商注册号': 'registration_no',
    '纳税人识别号': 'taxpayer_id', '纳税人编号': 'taxpayer_id',
    '英文名': 'english_name', '英文名称': 'english_name', '英文名字': 'english_name',
    '电话': 'phone', '联系电话': 'phone',
    '邮箱': 'email', '电子邮箱': 'email', '电子邮件': 'email',
    '网址': 'website', '网站': 'website', '官网': 'website',
    '所属地区': 'region',
  };
  return map[label] || null;
}

// 从切分好的段里提取最终值
function parseTextToFields(text) {
  if (!text || !text.trim()) return {};

  // 企业名称特殊处理：先从文本开头找含"公司"后缀的词
  const result = {};

  // 1. 按字段标签切分文本
  const segments = _splitByLabels(text);

  // 2. 从切分好的段里提取具体值
  // 法人：只取2-4个汉字
  if (segments.legal_person) {
    const lp = segments.legal_person.match(/^([\u4e00-\u9fa5]{2,4})/);
    if (lp) result.legal_person = lp[1];
  }

  // 经营状态：精确匹配关键词
  if (segments.business_status) {
    const bs = segments.business_status.match(/(存续|在业|在营|开业|吊销|注销|迁出|停业|清算|活跃|正常经营)/);
    if (bs) result.business_status = bs[1];
  }

  // 注册资本：匹配数字+万元
  if (segments.registered_capital) {
    const rc = segments.registered_capital.match(/([\d,.]+\s*万[元人民币]*)/);
    if (rc) result.registered_capital = rc[1];
  }

  // 实缴资本
  if (segments.paid_capital) {
    const pc = segments.paid_capital.match(/([\d,.]+\s*万[元人民币]*)/);
    if (pc) result.paid_capital = pc[1];
  }

  // 成立日期：统一格式
  if (segments.established_date) {
    const ed = segments.established_date.match(/(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)/);
    if (ed) result.established_date = ed[1].replace(/[/年月]/g, '-').replace(/日$/, '');
  }

  // 核准日期
  if (segments.approved_date) {
    const ad = segments.approved_date.match(/(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)/);
    if (ad) result.approved_date = ad[1].replace(/[/年月]/g, '-').replace(/日$/, '');
  }

  // 营业期限：直接用切分好的值（已清洗）
  if (segments.business_term) result.business_term = segments.business_term;

  // 公司类型：直接用切分好的值
  if (segments.company_type) result.company_type = segments.company_type;

  // 所属行业：直接用切分好的值
  if (segments.industry) result.industry = segments.industry;

  // 参保人数：取数字，去掉“（N年年报）”等附加信息
  if (segments.insured_count) {
    const ic = segments.insured_count.match(/(\d+)/);
    if (ic) result.insured_count = ic[1];
  }

  // 注册地址：直接用切分好的值
  if (segments.address) result.address = segments.address;

  // 经营范围：直接用切分好的值（可能很长，截断到300字）
  if (segments.business_scope) {
    let scope = segments.business_scope;
    if (scope.length > 300) scope = scope.substring(0, 300);
    result.business_scope = scope;
  }

  // 信用代码：取18位
  if (segments.credit_code) {
    const cc = segments.credit_code.match(/([0-9A-Za-z]{18})/);
    if (cc) result.credit_code = cc[1];
  }

  // 组织机构代码
  if (segments.org_code) {
    const org = segments.org_code.match(/([0-9A-Za-z-]{8,12})/);
    if (org) result.org_code = org[1];
  }

  // 注册号：取15位数字（从切分段提取，不受后面粘连文字影响）
  if (segments.registration_no) {
    const reg = segments.registration_no.match(/(\d{15})/);
    if (reg) result.registration_no = reg[1];
  }

  // 英文名：值为“暂无”或“-”时不输出
  if (segments.english_name && !/^(暂无|-)$/.test(segments.english_name.trim())) {
    result.english_name = segments.english_name.trim();
  }

  // 通信地址
  if (segments.mailing_address) result.mailing_address = segments.mailing_address;

  // 曾用名：从文本中提取所有曾用名（可能有多个）
  const formerNames = [];
  const formerRe = /曾用名[：:]\s*([\u4e00-\u9fa5]{2,30}(?:有限公司|股份有限公司|有限责任公司|合伙企业|集团))/g;
  let fnMatch;
  while ((fnMatch = formerRe.exec(text)) !== null) {
    if (!formerNames.includes(fnMatch[1])) formerNames.push(fnMatch[1]);
  }
  if (formerNames.length > 0) result.former_name = formerNames.join('; ');

  // 企业名称：优先从切分段取，否则从文本开头找
  if (segments.name) {
    result.name = segments.name;
  } else {
    const m = text.match(/([\u4e00-\u9fa5]{2,30}(?:有限公司|股份有限公司|有限责任公司|合伙企业|集团))/);
    if (m) result.name = cleanText(m[1]);
  }

  // 电话和邮箱：由按钮点击时的 extractAllContacts() 提取并覆盖，不在文本解析中提取
  // 如果是独立调用 parseTextToFields（非按钮触发），则从文本切分段补充提取
  if (segments.phone) {
    const phones = [];
    const segPhones = segments.phone.match(/1[3-9]\d{9}/g);
    if (segPhones) {
      for (const sp of segPhones) {
        if (_isPhone(sp) && !phones.includes(sp)) phones.push(sp);
      }
    }
    // 多号码粘连分割
    if (phones.length === 0) {
      const longNum = segments.phone.replace(/\D/g, '');
      if (longNum.length >= 11) {
        const re11 = /1[3-9]\d{9}/g;
        let m11;
        while ((m11 = re11.exec(longNum)) !== null) {
          if (_isPhone(m11[0]) && !phones.includes(m11[0])) phones.push(m11[0]);
        }
      }
    }
    if (phones.length > 0) result.phone = phones.join('; ');
  }

  // 邮箱：从全文用正则取（去重）
  if (!result.email) {
    const emailMatches = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g);
    if (emailMatches) {
      const unique = [...new Set(emailMatches)];
      result.email = unique.join('; ');
    }
  }

  // 官网：值为“暂无”或“-”时不输出，值中截取到第一个非地址文字
  if (segments.website) {
    const wv = segments.website.trim();
    if (!/^(暂无|-)$/.test(wv)) {
      // 如果值以“暂无”开头，取“暂无”
      if (wv.startsWith('暂无')) { /* 不输出 */ }
      else {
        // 截取：只保留 URL 格式的部分
        const urlMatch = wv.match(/(https?:\/\/[^\s]+|www\.[^\s]+)/);
        if (urlMatch) result.website = urlMatch[1];
        else if (wv.length <= 50) result.website = wv;
      }
    }
  }

  // 股东提取
  result.shareholders = _extractShareholdersJS(text);

  // 纳税人识别号：如果有独立字段就用，否则用信用代码
  if (segments.taxpayer_id) {
    const tid = segments.taxpayer_id.match(/([0-9A-Za-z]{15,20})/);
    if (tid) result.taxpayer_id = tid[1];
  } else if (result.credit_code) {
    result.taxpayer_id = result.credit_code;
  }

  // 从地址中补充省/市/区
  // 通用规则：按行政区划后缀来匹配，适配任何平台
  if (result.address) {
    let addr = result.address;

    // 省：优先匹配“省”或“自治区”或“特别行政区”（不含“市”，避免误吃地级市）
    let provMatch = addr.match(/([\u4e00-\u9fa5]{2,8}(?:省|自治区|特别行政区))/);
    // 如果没匹配到省，可能是直辖市，再匹配“市”
    if (!provMatch) {
      provMatch = addr.match(/([\u4e00-\u9fa5]{2,4}市)/);
    }
    if (provMatch) {
      result.province = provMatch[1];
      addr = addr.replace(provMatch[1], '');
    }

    // 市：匹配“XX市”或“XX自治州”或“XX地区”或“XX盟”
    const cityMatch = addr.match(/([\u4e00-\u9fa5]{2,8}(?:市|自治州|地区|盟))/);
    if (cityMatch) {
      result.city = cityMatch[1];
      addr = addr.replace(cityMatch[1], '');
    }

    // 区县：匹配"XX区"或"XX县"或"XX旗"或"XX市"（县级市）
    const districtMatch = addr.match(/([\u4e00-\u9fa5]{2,6}(?:区|县|旗|市))/);
    if (districtMatch) {
      result.district = districtMatch[1];
    }
  }

  return result;
}

// 插件版股东提取（JS 实现，跟 EntHub 后端逻辑一致）
function _extractShareholdersJS(text) {
  // 找到"股东信息"后面紧跟"序号"的位置
  let sectionStart = -1;
  let idx = text.indexOf('股东信息');
  while (idx >= 0) {
    const after = text.substring(idx + 4, idx + 204);
    if (after.includes('序号')) {
      sectionStart = idx;
      break;
    }
    const nextIdx = text.indexOf('股东信息', idx + 1);
    if (nextIdx < 0) break;
    idx = nextIdx;
  }
  if (sectionStart < 0) return '';

  let section = text.substring(sectionStart);
  // 截断到下一个大区块
  const markers = ['实际控制人', '受益所有人', '受益股东', '主要人员', '高级职员',
    '对外投资', '变更记录', '变更信息', '企业年报', '控制企业', '分支机构',
    '财务数据', '企业受益', '疑似实际', '最终受益', '同业分析', '关联方认定', '社保人数'];
  for (const marker of markers) {
    const idx2 = section.indexOf(marker, 20);
    if (idx2 >= 0) {
      section = section.substring(0, idx2);
      break;
    }
  }

  const shareholders = [];
  const seen = new Set();

  // 1. 提取公司名股东
  const companyRe = /([\u4e00-\u9fa5]{2,20}(?:公司|集团|有限|厂|店|社|院|事务所|工作室|商行|合伙)(?:企业)?(?:（有限合伙）|（自然人投资或控股）)?)/g;
  let cMatch;
  while ((cMatch = companyRe.exec(section)) !== null) {
    let name = cMatch[1].replace(/\n/g, '').trim();
    if (name.length < 4 || name.length > 30) continue;
    if (['有限合伙', '直接或间接拥有公司'].includes(name)) continue;
    if (['公告', '报告', '由公司', '第条', '条款', '条规定', '上市'].some(w => name.includes(w))) continue;
    if (!seen.has(name)) {
      seen.add(name);
      shareholders.push(name);
    }
  }

  // 2. 提取自然人股东（风鸟格式：序号 \n 姓 \n 全名 \n N家）
  const personRe = /(?:^|\n)\d+\s*\n[\u4e00-\u9fa5]\n([\u4e00-\u9fa5]{2,4})\n/g;
  let pMatch;
  while ((pMatch = personRe.exec(section)) !== null) {
    const name = pMatch[1];
    const nonNameWords = ['主要', '人员', '姓名', '序号', '职务', '详情', '历史', '关联', '企业',
      '法定', '代表', '股东', '认缴', '实缴', '持股', '出资', '股权', '比例', '日期', '名称',
      '董事', '监事', '经理', '员工', '实际', '控制', '受益', '所有', '变更', '投资',
      '分支', '机构', '年报', '工商', '登记', '信息', '查看', '更多', '展开', '收起',
      '下载', '复制', '编辑', '监控', '收藏', '导出', '记录', '查询'];
    if (nonNameWords.includes(name)) continue;
    if (!seen.has(name)) {
      seen.add(name);
      shareholders.push(name);
    }
  }

  return shareholders.join('; ');
}


// 风鸟等网站把额外电话藏在 hover 浮层、tooltip 等隐藏元素里
// 用 textContent（不受 CSS 隐藏影响）扫描整个 DOM，找出所有电话号码

function scanAllPhones() {
  // 备用方案：在工商信息区域扫描隐藏电话
  const PHONE_RE = /(?:1[3-9]\d{9})|(?:0\d{2,3}[-\s]?\d{7,8})|(?:0\d{9,11})/g;
  const phones = new Set();
  // 确定搜索范围：优先找工商信息容器
  let searchRoot = document.body;
  const bizContainer = document.querySelector(
    '[class*="basic"], [class*="baseInfo"], [class*="entInfo"], ' +
    '[class*="detail-info"], [class*="company-info"], [class*="ent-info"], ' +
    '[class*="business"], [class*="regist"], [class*="工商"], ' +
    'table, .info-table, .detail-table'
  );
  if (bizContainer) searchRoot = bizContainer;
  const walker = document.createTreeWalker(searchRoot, NodeFilter.SHOW_TEXT, null);
  while (walker.nextNode()) {
    const text = walker.currentNode.textContent || '';
    if (/(登录|注册|账号|密码|退出|用户名|验证码|我的|个人中心|会员)/.test(text)) continue;
    const matches = text.match(PHONE_RE);
    if (matches) {
      for (const p of matches) {
        if (_isPhone(p)) phones.add(p.trim());
      }
    }
  }
  return [...phones];
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
  const pageText = extractPageText();
  
  // 插件自己解析文本，不依赖 EntHub
  const parsedFields = parseTextToFields(pageText);
  
  // 合并：DOM 提取的字段优先于纯文本解析
  const mergedFields = { ...parsedFields };
  for (const [k, v] of Object.entries(data)) {
    if (v && !mergedFields[k]) mergedFields[k] = v;
  }

  if (fieldCount < 3) {
    return {
      method: 'text',
      source: host,
      text: pageText,
      fields: mergedFields,
    };
  }

  return {
    method: 'dom',
    source: source,
    text: pageText,
    fields: mergedFields,
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

// ════════════════════════════════════════════════════════════════════════════
// ── 页面内注入悬浮按钮（风鸟页面专用）────────────────────────────────────────
// ════════════════════════════════════════════════════════════════════════════

// 延迟执行：确保 document.body 存在后再注入
function startInject() {
  if (!location.hostname.includes('riskbird.com')) return;
  if (document.getElementById('enthub-float-btn')) return;
  if (!document.body) {
    // body 还没准备好，等一下再试
    setTimeout(startInject, 500);
    return;
  }

  function bgSend(msg) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(msg, (resp) => resolve(resp));
    });
  }

  function esc(text) {
    const d = document.createElement('div');
    d.textContent = text || '';
    return d.innerHTML;
  }

  // 获取 EntHub 服务地址（与 background.js 的 DEFAULT_ENTHUB_URL 一致）
  function getEntHubBaseUrl() {
    return new Promise((resolve) => {
      chrome.storage.local.get('enthub_url', (result) => {
        resolve(result.enthub_url || 'http://127.0.0.1:5210');
      });
    });
  }

  // ── 异步提取全部电话和邮箱（含 hover 浮层里的隐藏值）──
  // 风鸟用 Element Plus tooltip，同一时间只显示一个 popper
  // 所以必须逐个 hover “更多”按钮 → 等 popper 彈出 → 立即提取 → 再 hover 下一个
  async function extractAllContacts() {
    const phones = [];
    const emails = [];
    const seenPhones = new Set();
    const seenEmails = new Set();
    const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
    const PHONE_RE = /1[3-9]\d{9}/g;
    const LANDLINE_RE = /0\d{2,3}-\d{7,8}/g;

    function addPhone(p) {
      if (p && _isPhone(p) && !seenPhones.has(p)) { seenPhones.add(p); phones.push(p); }
    }
    function addEmail(e) {
      if (e && !seenEmails.has(e)) { seenEmails.add(e); emails.push(e); }
    }

    // 1. 先从 contact-box -> content-box 取第一个显示的号码/邮箱
    const contactBoxes = document.querySelectorAll('.contact-box');
    contactBoxes.forEach(box => {
      const contentBox = box.querySelector('.content-box');
      if (!contentBox) return;
      const text = (contentBox.textContent || '').trim();
      const pm = text.match(PHONE_RE);
      if (pm) pm.forEach(addPhone);
      const lm = text.match(LANDLINE_RE);
      if (lm) lm.forEach(addPhone);
      const em = text.match(EMAIL_RE);
      if (em) em.forEach(addEmail);
    });

    // 2. 逐个 hover “更多”按钮，每次等 popper 彈出后立即提取
    const moreBtns = document.querySelectorAll('.more-btn');
    for (const btn of moreBtns) {
      if (!/更多/.test(btn.textContent)) continue;

      // hover 该按钮
      btn.dispatchEvent(new MouseEvent('pointerenter', { bubbles: true }));
      btn.dispatchEvent(new MouseEvent('pointerover', { bubbles: true }));
      btn.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
      btn.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      btn.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }));

      // 等 popper 彈出
      await new Promise(r => setTimeout(r, 800));

      // 立即提取当前可见的 popper
      const poppers = document.querySelectorAll('.el-popper.is-light');
      for (const popper of poppers) {
        const text = (popper.textContent || '').trim();
        // 排除用户菜单 popper
        if (/个人中心|会员|积分|收藏|浏览历史|退出登录|账户余额|邀请/.test(text)) continue;

        // 从子 div 分别取值（避免多个号码粘连）
        const divs = popper.querySelectorAll('div');
        if (divs.length > 0) {
          divs.forEach(d => {
            const t = (d.textContent || '').trim();
            if (!t || t.includes('arrow')) return;
            const pm = t.match(PHONE_RE);
            if (pm) pm.forEach(addPhone);
            const lm = t.match(LANDLINE_RE);
            if (lm) lm.forEach(addPhone);
            const em = t.match(EMAIL_RE);
            if (em) em.forEach(addEmail);
          });
        }
        // fallback：仅当 div 级提取未取到任何值时，从整个 textContent 用正则提取
        if (phones.length === 0 && emails.length === 0) {
          const pm2 = text.match(PHONE_RE); if (pm2) pm2.forEach(addPhone);
          const lm2 = text.match(LANDLINE_RE); if (lm2) lm2.forEach(addPhone);
          const em2 = text.match(EMAIL_RE); if (em2) em2.forEach(addEmail);
        }
      }
    }

    // 3. 如果以上都没取到电话，从“电话”标签旁边的值元素取 textContent
    if (phones.length === 0) {
      const labels = document.querySelectorAll('td, th, .label, dt, [class*="label"]');
      labels.forEach(el => {
        const t = (el.textContent || '').trim();
        if (!/^电话$|^联系电话$/.test(t)) return;
        const sib = el.nextElementSibling;
        if (!sib) return;
        const rawText = sib.textContent || '';
        const found = rawText.match(PHONE_RE);
        if (found) found.forEach(addPhone);
      });
    }

    return { phones, emails };
  }

  // ── 注入 CSS ──
  const style = document.createElement('style');
  style.textContent = `
    /* 容器：主按钮 + 关闭按钮横排，不会重叠 */
    #enthub-btn-wrap {
      position: fixed; right: 20px; bottom: 80px; z-index: 2147483647;
      display: flex; align-items: center; gap: 10px;
      font-family: -apple-system, "PingFang SC", sans-serif;
    }

    /* 椭圆形主按钮 */
    #enthub-float-btn {
      display: flex; align-items: center; gap: 6px;
      padding: 8px 16px 8px 10px; border-radius: 24px;
      background: #D97757; color: #fff; font-size: 13px; font-weight: 600;
      cursor: pointer; border: none; box-shadow: 0 4px 16px rgba(217,119,87,0.4);
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      user-select: none;
    }
    #enthub-float-btn:hover { background: #C9663F; transform: translateY(-2px); box-shadow: 0 6px 20px rgba(217,119,87,0.5); }
    #enthub-float-btn:active { transform: translateY(0); }
    #enthub-float-btn:disabled { opacity: 0.6; cursor: wait; }
    #enthub-float-btn img { width: 18px; height: 18px; border-radius: 4px; flex-shrink: 0; }
    #enthub-float-btn .enthub-spinner {
      width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3);
      border-top-color: #fff; border-radius: 50%; animation: enthub-spin 0.6s linear infinite;
      flex-shrink: 0;
    }
    #enthub-float-btn.enthub-active {
      background: #5C564E; box-shadow: 0 2px 8px rgba(92,86,78,0.3);
    }
    @keyframes enthub-spin { to { transform: rotate(360deg); } }

    /* 圆形关闭按钮 */
    #enthub-close-btn {
      width: 36px; height: 36px; min-width: 36px; border-radius: 50%;
      background: #fff; border: 2px solid #ECE7DF;
      cursor: pointer; display: flex; align-items: center; justify-content: center;
      box-shadow: 0 2px 12px rgba(0,0,0,0.12);
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      opacity: 0; transform: scale(0.5); pointer-events: none;
      padding: 0;
    }
    #enthub-close-btn.enthub-show {
      opacity: 1; transform: scale(1); pointer-events: auto;
    }
    #enthub-close-btn:hover {
      border-color: #D97757; transform: scale(1.1); box-shadow: 0 4px 16px rgba(217,119,87,0.3);
    }
    #enthub-close-btn:active { transform: scale(0.95); }
    #enthub-close-btn svg {
      width: 16px; height: 16px; stroke: #5C564E;
      transition: stroke 0.2s; animation: enthub-spin-slow 3s linear infinite;
    }
    #enthub-close-btn:hover svg { stroke: #D97757; }
    @keyframes enthub-spin-slow { to { transform: rotate(360deg); } }

    #enthub-panel {
      position: fixed; right: 20px; bottom: 130px; z-index: 2147483647;
      width: 380px; max-height: 520px; overflow-y: auto;
      background: #fff; border-radius: 12px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.18);
      font-family: -apple-system, "PingFang SC", sans-serif; font-size: 13px;
      color: #1F1B17; display: none;
      animation: enthub-panel-in 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    @keyframes enthub-panel-in {
      from { opacity: 0; transform: translateY(10px) scale(0.96); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }
    #enthub-panel.enthub-show { display: block; }
    #enthub-panel .enthub-panel-header {
      display: flex; justify-content: space-between; align-items: center;
      padding: 12px 16px; border-bottom: 1px solid #ECE7DF;
      font-weight: 700; font-size: 14px;
    }
    #enthub-panel .enthub-panel-close {
      background: none; border: none; font-size: 16px; cursor: pointer;
      color: #A39E96; padding: 2px 6px;
    }
    #enthub-panel .enthub-panel-close:hover { color: #C2410C; }
    #enthub-panel .enthub-panel-body { padding: 8px 16px 16px; }
    #enthub-panel .enthub-group-title {
      font-size: 12px; font-weight: 700; color: #D97757;
      margin: 10px 0 4px; padding-bottom: 3px; border-bottom: 1px solid #F5F2ED;
    }
    #enthub-panel .enthub-group-title:first-child { margin-top: 4px; }
    #enthub-panel .enthub-field-row {
      display: flex; align-items: center; margin-bottom: 3px; padding: 2px 0;
    }
    #enthub-panel .enthub-field-label {
      width: 90px; min-width: 90px; font-size: 11px; color: #5C564E; font-weight: 500;
    }
    #enthub-panel .enthub-field-value { flex: 1; min-width: 0; }
    #enthub-panel .enthub-field-value input {
      width: 100%; border: 1px solid transparent; padding: 2px 4px;
      border-radius: 3px; font-size: 12px; color: #1F1B17; background: transparent;
      font-family: inherit;
    }
    #enthub-panel .enthub-field-value input:hover { border-color: #ECE7DF; }
    #enthub-panel .enthub-field-value input:focus { outline: none; border-color: #D97757; background: #fff; }
    #enthub-panel .enthub-field-empty {
      font-size: 12px; color: #D0CCC4; font-style: italic; padding: 2px 4px;
    }
    #enthub-panel .enthub-meta {
      font-size: 11px; color: #A39E96; margin-bottom: 4px;
    }
    #enthub-panel .enthub-alert {
      padding: 8px 10px; border-radius: 6px; font-size: 12px; margin-bottom: 8px; line-height: 1.5;
    }
    #enthub-panel .enthub-alert a { font-weight: 600; }
    #enthub-panel .enthub-alert a:hover { text-decoration: underline; }
    #enthub-panel .enthub-alert-warning { background: #FEF3C7; color: #B45309; }
    #enthub-panel .enthub-alert-success { background: #ECFDF3; color: #16A34A; }
    #enthub-panel .enthub-alert-error { background: #FEF1EB; color: #C2410C; }
    #enthub-panel .enthub-btn-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
    #enthub-panel .enthub-btn {
      padding: 6px 14px; border-radius: 5px; font-size: 12px; font-weight: 500;
      cursor: pointer; border: 1px solid transparent; font-family: inherit; white-space: nowrap;
    }
    #enthub-panel .enthub-btn-primary { background: #D97757; color: #fff; border-color: #D97757; }
    #enthub-panel .enthub-btn-primary:hover { background: #C9663F; }
    #enthub-panel .enthub-btn-primary:disabled { opacity: 0.5; cursor: wait; }
    #enthub-panel .enthub-btn-warning { background: #fff; color: #B45309; border: 1px solid #B45309; }
    #enthub-panel .enthub-btn-warning:hover { background: #B45309; color: #fff; }
    #enthub-panel .enthub-btn-warning:disabled { opacity: 0.5; cursor: wait; }
    #enthub-panel .enthub-btn-link {
      background: none; border: none; font-size: 12px; color: #D97757; cursor: pointer;
      text-decoration: none; font-family: inherit; padding: 4px 0;
    }
    #enthub-panel .enthub-btn-link:hover { text-decoration: underline; }
    #enthub-panel .enthub-hint { font-size: 11px; color: #A39E96; margin-top: 4px; line-height: 1.3; }
  `;
  document.head.appendChild(style);

  // ── 字段分组（与 EntHub 表单一致）──
  const FIELD_GROUPS = [
    { title: '基本信息', fields: [
      ['name', '企业名称'], ['former_name', '曾用名'], ['business_status', '经营状态'],
      ['legal_person', '法定代表人'], ['registered_capital', '注册资本'],
      ['paid_capital', '实缴资本'], ['established_date', '成立日期'],
      ['approved_date', '核准日期'], ['business_term', '营业期限'],
      ['company_type', '公司类型'], ['industry', '所属行业'],
      ['insured_count', '参保人数'], ['shareholders', '股东'],
    ]},
    { title: '联系方式', fields: [
      ['phone', '电话'], ['email', '邮箱'], ['website', '网址'],
    ]},
    { title: '地址信息', fields: [
      ['province', '省份'], ['city', '城市'], ['district', '区县'],
      ['address', '注册地址'],
    ]},
    { title: '工商注册信息', fields: [
      ['credit_code', '统一社会信用代码'], ['registration_no', '注册号'],
      ['org_code', '组织机构代码'],
    ]},
    { title: '经营范围', fields: [
      ['business_scope', '经营范围'],
    ]},
  ];

  // ── 注入容器（包住主按钮+关闭按钮，flex横排不重叠）──
  const btnWrap = document.createElement('div');
  btnWrap.id = 'enthub-btn-wrap';
  document.body.appendChild(btnWrap);

  // ── 注入主按钮（椭圆形，带APP图标）──
  const btn = document.createElement('button');
  btn.id = 'enthub-float-btn';
  btn.innerHTML = '<img src="' + chrome.runtime.getURL('icons/icon16.png') + '"><span>EntHub</span>';
  btnWrap.appendChild(btn);

  // ── 注入圆形关闭按钮（面板打开时出现，带旋转动效）──
  const closeBtn = document.createElement('button');
  closeBtn.id = 'enthub-close-btn';
  closeBtn.title = '关闭';
  closeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>';
  btnWrap.appendChild(closeBtn);

  // ── 注入面板（去掉旧的关闭按钮）──
  const panel = document.createElement('div');
  panel.id = 'enthub-panel';
  panel.innerHTML = `
    <div class="enthub-panel-header">
      <span>EntHub 提取结果</span>
    </div>
    <div class="enthub-panel-body" id="enthub-panel-body">
      <div class="enthub-meta" id="enthub-meta"></div>
      <div id="enthub-alerts"></div>
      <div id="enthub-fields"></div>
      <div class="enthub-btn-row" id="enthub-btn-row"></div>
    </div>
  `;
  document.body.appendChild(panel);

  // ── 状态 ──
  let grabbedData = null;
  let extractedFields = {};
  let existingCompany = null;

  // ── 渲染字段（按 EntHub 分组，空字段也显示）──
  function renderFields(fields) {
    const container = document.getElementById('enthub-fields');
    if (!container) return;
    container.innerHTML = '';

    for (const group of FIELD_GROUPS) {
      // 分组标题
      const titleDiv = document.createElement('div');
      titleDiv.className = 'enthub-group-title';
      titleDiv.textContent = group.title;
      container.appendChild(titleDiv);

      for (const [key, label] of group.fields) {
        const value = fields[key];
        const hasValue = value !== undefined && value !== '' && value !== null;
        const row = document.createElement('div');
        row.className = 'enthub-field-row';
        if (hasValue) {
          row.innerHTML = `
            <div class="enthub-field-label">${label}</div>
            <div class="enthub-field-value">
              <input type="text" value="${esc(value)}" data-field="${key}">
            </div>`;
        } else {
          row.innerHTML = `
            <div class="enthub-field-label">${label}</div>
            <div class="enthub-field-value">
              <span class="enthub-field-empty">—</span>
            </div>`;
        }
        container.appendChild(row);
      }
    }

    // 编辑监听
    container.querySelectorAll('input[data-field]').forEach(input => {
      input.addEventListener('change', (e) => {
        extractedFields[e.target.dataset.field] = e.target.value.trim();
      });
    });
  }

  function renderButtons() {
    const row = document.getElementById('enthub-btn-row');
    if (!row) return;
    row.innerHTML = '';
    if (existingCompany) {
      // 重复企业：紧凑按钮行
      const b = document.createElement('button');
      b.className = 'enthub-btn enthub-btn-warning';
      b.textContent = '覆盖更新';
      b.addEventListener('click', () => doSubmit(true));
      row.appendChild(b);
      const link = document.createElement('a');
      link.className = 'enthub-btn-link';
      link.textContent = '查看已有 →';
      link.href = '#';
      link.addEventListener('click', async (e) => {
        e.preventDefault();
        const url = await getEntHubBaseUrl();
        window.open(`${url}/company/${existingCompany.id}`, '_blank');
      });
      row.appendChild(link);
      const hint = document.createElement('div');
      hint.className = 'enthub-hint';
      hint.textContent = '电话/邮箱/股东只追加不删除';
      row.appendChild(hint);
    } else if (extractedFields.name) {
      const b = document.createElement('button');
      b.className = 'enthub-btn enthub-btn-primary';
      b.textContent = '确认录入';
      b.addEventListener('click', () => doSubmit(false));
      row.appendChild(b);
    }
  }

  function showAlert(type, html) {
    const alerts = document.getElementById('enthub-alerts');
    if (!alerts) return;
    const div = document.createElement('div');
    div.className = `enthub-alert enthub-alert-${type}`;
    div.innerHTML = html;
    alerts.appendChild(div);
    return div;
  }

  function clearAlerts() {
    const el = document.getElementById('enthub-alerts');
    if (el) el.innerHTML = '';
  }

  // ── 按钮点击：先 hover 提取联系信息 → 抓取页面文本 → 解析字段 ──
  btn.addEventListener('click', async () => {
    // 如果面板已打开，点击主按钮关闭
    if (panel.classList.contains('enthub-show')) {
      panel.classList.remove('enthub-show');
      closeBtn.classList.remove('enthub-show');
      btn.classList.remove('enthub-active');
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="enthub-spinner"></span>';

    try {
      // 1. 异步提取全部电话和邮箱（含 hover “更多”浮层里的隐藏值）
      const contacts = await extractAllContacts();

      // 2. 抓取页面文本
      const data = extractCompanyInfo();
      grabbedData = data;

      // 3. 插件自己解析文本，不调 EntHub
      extractedFields = parseTextToFields(data.text);

      // 4. 用 DOM 提取的联系信息覆盖文本解析结果
      if (contacts.phones.length > 0) {
        extractedFields.phone = contacts.phones.join('; ');
      }
      if (contacts.emails.length > 0) {
        extractedFields.email = contacts.emails.join('; ');
      }

      // 5. DOM 提取的其他字段合并过来（DOM 优先）
      if (data.fields && Object.keys(data.fields).length > 0) {
        for (const [k, v] of Object.entries(data.fields)) {
          if (v && !extractedFields[k]) extractedFields[k] = v;
        }
      }

      const fieldCount = Object.keys(extractedFields).length;
      document.getElementById('enthub-meta').textContent =
        `插件解析 · ${fieldCount} 个字段`;

      clearAlerts();
      renderFields(extractedFields);
      renderButtons();
      panel.classList.add('enthub-show');
      closeBtn.classList.add('enthub-show');
      btn.classList.add('enthub-active');
    } catch (e) {
      clearAlerts();
      showAlert('error', `❌ ${esc(e.message)}`);
      renderFields({});
      renderButtons();
      panel.classList.add('enthub-show');
      closeBtn.classList.add('enthub-show');
      btn.classList.add('enthub-active');
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<img src="' + chrome.runtime.getURL('icons/icon16.png') + '"><span>EntHub</span>';
    }
  });

  // ── 提交保存 ──
  async function doSubmit(overwrite) {
    document.querySelectorAll('#enthub-fields input[data-field]').forEach(input => {
      extractedFields[input.dataset.field] = input.value.trim();
    });

    if (!extractedFields.name) {
      clearAlerts();
      showAlert('error', '缺少企业名称');
      return;
    }

    const buttons = document.querySelectorAll('#enthub-btn-row .enthub-btn');
    buttons.forEach(b => b.disabled = true);

    try {
      const resp = await bgSend({
        action: 'submit',
        text: grabbedData?.text || '',
        method: 'auto',
        fields: extractedFields,
        overwrite: overwrite,
      });

      if (!resp || resp.code !== 0) throw new Error(resp?.message || '提交失败');

      const data = resp.data;
      if (data.action === 'exists') {
        clearAlerts();
        showAlert('warning', `已存在: <strong>${esc(data.existing_name)}</strong>`);
        existingCompany = { id: data.existing_id, name: data.existing_name };
        renderButtons();
        return;
      }

      clearAlerts();
      const actionText = data.action === 'created' ? '已录入' : '已更新';
      showAlert('success', `${actionText}: <strong>${esc(data.name)}</strong>`);
      // 录入成功后只显示跳转链接
      const row = document.getElementById('enthub-btn-row');
      if (row) {
        row.innerHTML = '';
        const url = await getEntHubBaseUrl();
        const link = document.createElement('a');
        link.className = 'enthub-btn-link';
        link.textContent = '查看详情 →';
        link.href = `${url}/company/${data.id}`;
        link.target = '_blank';
        row.appendChild(link);
      }
      document.getElementById('enthub-fields').innerHTML = '';
    } catch (e) {
      clearAlerts();
      showAlert('error', `❌ ${esc(e.message)}`);
      buttons.forEach(b => b.disabled = false);
    }
  }

  // ── 关闭面板：圆形按钮点击 ──
  closeBtn.addEventListener('click', () => {
    panel.classList.remove('enthub-show');
    closeBtn.classList.remove('enthub-show');
    btn.classList.remove('enthub-active');
  });

  // ── SPA 路由切换时重新挂载 ──
  let lastUrl = location.href;
  const observer = new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      panel.classList.remove('enthub-show');
      closeBtn.classList.remove('enthub-show');
      btn.classList.remove('enthub-active');
    }
    if (!document.getElementById('enthub-btn-wrap')) document.body.appendChild(btnWrap);
    if (!document.getElementById('enthub-panel')) document.body.appendChild(panel);
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

// 启动注入
startInject();
