# 代码恢复记录

> 本文件用于记录因 2026-08-07 误操作回退（git restore）丢失的代码改动。
> 最后一次 git 提交时间：2026-08-05 16:23
> 回退发生时间：2026-08-07 上午
>
> 各历史对话的 AI 会将丢失的改动追加到本文件末尾。
> 请勿覆盖已有内容，仅追加。

---

## 对话时间：2026-08-07
## 改动概述：电话主号体系 + 导入字段匹配 + 标签bug修复 + UI调整，共涉及11个文件

### 排查结论：✅ 全部未丢失

本次对话修改的 11 个文件逐一核验，当前文件内容与对话最终状态完全一致，**无任何改动丢失**。

具体核验清单：

1. **`utils.py`** ✅ — `CONTAINS_ALIASES` 有 `("地址（注册", "address")` 和 `("地址（通信", "mailing_address")`；`SECONDARY_PHONE_PATTERN` 为 `r"^(联系电话\d+|更多电话)$"`
2. **`templates/company_detail.html`** ✅ — 主号行有 `<span class="phone-primary-star">★</span>`；标记弹窗有"设为主号"按钮和 `is-primary` 查询；`startPress` 在按下时立即挂载 `endPress` 到 document；长按阈值 700ms；`setAsPrimaryPhone` JS 函数存在
3. **`data_helpers.py`** ✅ — `sync_phones` 有 `old_primary_norm` 保留逻辑 + `recommended` 优先选主号；`merge_phones` 有 `recommended` 优先逻辑
4. **`db.py`** ✅ — `CREATE TABLE company_phones` 无 `is_recommended` 列；无 `sort_order` 迁移；有 `_try_drop_column` 函数和对 `sort_order`/`is_recommended` 的 DROP COLUMN 调用
5. **`routes/import_flow.py`** ✅ — 新公司插入有 `_recommended` + `primary_norm` 优先级逻辑；`_merge_phones_cached` 有 `needs_primary` + `recommended` 优先逻辑
6. **`routes/companies.py`** ✅ — `reorder_phones` API 已删除；有 `check_primary_phone` GET 和 `set_primary_phone` POST 两个 API；详情查询 `ORDER BY cp.is_primary DESC, cp.id`；排序逻辑为主号排第一
7. **`routes/pages.py`** ✅ — phone 子查询有 `ORDER BY p.is_primary DESC, p.id`
8. **`mcp_server.py`** ✅ — 所有 `ORDER BY is_primary DESC` 已改为 `ORDER BY is_primary DESC, id`（含 cp/cp2 前缀变体）
9. **`static/style.css`** ✅ — 有 `.phone-primary-star` 样式；`.wechat-tip` 背景为 `#fff` + 边框 `#e5e7eb` + 按状态着色 hover 规则
10. **`templates/base.html`** ✅ — SPA 加载遮罩文字为"加载中…"
11. **`scripts/migrate_primary_phones.py`** ✅ — 文件存在，内容完整

---

## 对话时间：2026-08-07
## 改动概述：发送到多维表功能改造 — payload 字段名修正 + 前端弹窗增加数据预览和跟进记录

### 文件：`/Users/gm/AI/EntHub/routes/companies.py`

**改动位置**：`send_to_kinboard` 函数，约 1018-1030 行（payload 构造部分）

**改动说明**：
1. payload 字段名 `"公司名"` 改为 `"企业名称"`（与多维表列名对齐）
2. 新增从请求体读取 `follow_up` 字段（跟进记录）
3. payload 新增 `"跟进记录": follow_up` 字段

**最终代码**（本对话结束时此区域的最终状态）：

```python
    # 说明 + 跟进记录：从 AJAX 请求体中获取（用户在弹窗中输入，可选）
    data = request.get_json(silent=True) or {}
    remark = (data.get("remark") or "").strip()
    follow_up = (data.get("follow_up") or "").strip()

    payload = {
        "企业名称": name,
        "地址": address,
        "法人": legal_person,
        "主电话": primary_phone,
        "其他电话": other_phones_str,
        "备注": notes,
        "说明": remark,
        "跟进记录": follow_up,
    }
```

---

### 文件：`/Users/gm/AI/EntHub/templates/company_detail.html`

**改动位置**：`openKinboardModal` 函数（IIFE 内，`{% block scripts %}` 区域末尾，约 1432-1480 行）

**改动说明**：
1. 弹窗新增「数据预览」区块：用 Jinja2 `|tojson` 安全输出企业名称、地址、法人、主电话、其他电话、备注，供用户发送前核对
2. 弹窗新增「跟进记录」textarea（`#kinboard-follow-up`），聚焦优先级高于「说明」
3. 发送请求体新增 `follow_up` 字段，与后端 `跟进记录` 对应
4. 「说明」textarea 行数从 3 改为 2（让位给跟进记录）

**最终代码**（本对话结束时此区域的最终状态）：

```javascript
// ── 发送到多维表弹窗 ──
function openKinboardModal(companyId) {
    // ── 预览数据（从模板变量生成）──
    const _companyName = {{ company.name|tojson }};
    const _address = {{ (company.annual_report_address or company.address or "")|tojson }};
    const _legalPersonRaw = {{ (company.legal_person or "")|tojson }};
    const _legalPerson = _legalPersonRaw ? _legalPersonRaw + '（法人）' : '';

    // 电话列表
    const _phones = [
        {% for cp in company_phones %}
        { n: {{ cp.phone|tojson }}, p: {{ cp.is_primary|tojson }}, t: {{ (cp.phone_type or '')|tojson }} },
        {% endfor %}
    ];
    let _primaryPhone = '';
    const _allPhones = [];
    for (const ph of _phones) {
        _allPhones.push(ph.n);
        if (ph.p && !_primaryPhone && (ph.t === 'mobile' || ph.t === 'mobile_ext')) {
            _primaryPhone = ph.n;
        }
    }
    const _otherPhones = _allPhones.join('\n');

    // 备注：注册日期 + 资本（实缴）
    const _estDate = {{ (company.established_date or "")|tojson }};
    const _regCap = {{ (company.registered_capital or "")|tojson }}.replace(/人民币/g, '').replace(/\s/g, '').trim();
    const _paidCap = {{ (company.paid_capital or "")|tojson }}.replace(/人民币/g, '').replace(/\s/g, '').trim();
    const _notesParts = [];
    if (_estDate) _notesParts.push('注册日期：' + _estDate);
    if (_regCap && _paidCap) _notesParts.push('资本：' + _regCap + '（实缴' + _paidCap + '）');
    else if (_regCap) _notesParts.push('资本：' + _regCap);
    else if (_paidCap) _notesParts.push('实缴：' + _paidCap);
    const _notes = _notesParts.join('；');

    // ── 弹窗内容 ──
    const previewHtml = `
        <div style="margin-bottom: 14px; padding: 12px; background: var(--bg-secondary, #f9fafb); border-radius: 8px; font-size: 12px; line-height: 1.8;">
            <div style="font-weight: 600; margin-bottom: 6px; color: var(--text-muted);">数据预览</div>
            <div><span style="color: var(--text-muted); display: inline-block; min-width: 64px;">企业名称</span>${_companyName}</div>
            <div><span style="color: var(--text-muted); display: inline-block; min-width: 64px;">地址</span>${_address}</div>
            <div><span style="color: var(--text-muted); display: inline-block; min-width: 64px;">法人</span>${_legalPerson}</div>
            <div><span style="color: var(--text-muted); display: inline-block; min-width: 64px;">主电话</span>${_primaryPhone || '—'}</div>
            <div style="display: flex;"><span style="color: var(--text-muted); display: inline-block; min-width: 64px; flex-shrink: 0;">其他电话</span><pre style="margin: 0; white-space: pre-wrap; font-family: inherit; flex: 1;">${_otherPhones || '—'}</pre></div>
            <div><span style="color: var(--text-muted); display: inline-block; min-width: 64px;">备注</span>${_notes || '—'}</div>
        </div>
    `;

    const content = previewHtml + `
        <label style="display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; color: var(--text-primary);">跟进记录</label>
        <textarea id="kinboard-follow-up" rows="3" placeholder="记录本次跟进情况…"
                  style="width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; resize: none; box-sizing: border-box; line-height: 1.5; margin-bottom: 12px;"></textarea>
        <label style="display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; color: var(--text-primary);">说明</label>
        <textarea id="kinboard-remark" rows="2"
                  style="width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; resize: none; box-sizing: border-box; line-height: 1.5;"></textarea>
    `;
    const buttons = [
        { text: '取消', class: 'btn btn-secondary', action: 'closeModal()' },
        { text: '发送', class: 'btn btn-primary', action: '' }
    ];

    showModal('发送到多维表', content, buttons);

    // 聚焦跟进记录输入框
    setTimeout(() => {
        const ta = document.getElementById('kinboard-follow-up');
        if (ta) ta.focus();
    }, 100);

    // 绑定发送按钮
    const sendBtn = document.querySelector('#modal-footer .btn-primary');
    sendBtn.onclick = async () => {
        const followUp = document.getElementById('kinboard-follow-up').value;
        const remark = document.getElementById('kinboard-remark').value;
        const originalText = sendBtn.textContent;
        sendBtn.textContent = '发送中…';
        sendBtn.disabled = true;

        try {
            const resp = await fetch(`/company/${companyId}/send-to-kinboard`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ remark: remark, follow_up: followUp })
            });
            const data = await resp.json();
            if (data.code === 0) {
                _enthubToast('success', '✅ 发送成功', data.message);
                closeModal();
            } else {
                _enthubToast('error', '❌ 发送失败', data.message);
            }
        } catch (e) {
            _enthubToast('error', '❌ 发送失败', e.message);
        } finally {
            sendBtn.textContent = originalText;
            sendBtn.disabled = false;
        }
    };
}
window.openKinboardModal = openKinboardModal;
```

---

## 对话时间：2026-08-07
## 改动概述：联系方式区电话/邮箱卡片改为 Grid 两列等宽布局，加 padding-right 对齐上方「工商主体」「资本」卡片边缘

### 排查结论：⚠️ 部分丢失

Grid 两列布局（`display: grid; grid-template-columns: 1fr 1fr`）已保留，但 **`padding-right: 12px` 及两个移动端的 `padding-right: 0` 重置全部丢失**。此外 `gap` 从 `12px` 被改为 `10px`（可能是其他会话改动）。详细注释也被替换为简短注释。

---

### 文件：`/Users/gm/AI/EntHub/static/style.css`

**改动位置1**：桌面端 `.phone-list` 主样式（约第 1088-1093 行，`/* Phone list in company detail */` 注释下方）

**改动说明**：
将 `.phone-list` 从 flexbox 改为 CSS Grid 固定两列，并添加 `padding-right: 12px` 补偿上方三列布局（工商主体/资本/日期，flex 2:1:1，两个 12px gap）中缺失的第三列间隙。这样电话/邮箱卡片的左卡右边缘对齐「工商主体」右边缘，右卡左边缘对齐「资本」左边缘，中间间隙 12px 与上方一致，两卡等宽等高。gap 设为 12px 与上方 `detail-groups` 的 gap 保持一致。

**最终代码**（本对话结束时此区域的最终状态）：

```css
/* Phone/email list in company detail — 用 Grid 固定两列，通过 padding-right 补偿上方三列布局中
   缺失的第三列间隙，使列边界与「工商主体」「资本」卡片的边缘垂直对齐 */
.phone-list {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    padding-right: 12px;
}
```

---

### 文件：`/Users/gm/AI/EntHub/static/style.css`

**改动位置2**：768px 移动端断点（约第 982 行，`@media (max-width: 768px)` 内）

**改动说明**：
移动端 `.phone-list` 改为单列，同时重置 `padding-right: 0`（取消桌面端的补偿内边距）。

**最终代码**（本对话结束时此区域的最终状态）：

```css
    .phone-list { grid-template-columns: 1fr; padding-right: 0; }
```

---

### 文件：`/Users/gm/AI/EntHub/static/style.css`

**改动位置3**：480px 移动端断点（约第 3127 行，`@media (max-width: 480px)` 内）

**改动说明**：
更小屏幕下同样单列 + `gap: 8px`，并重置 `padding-right: 0`。

**最终代码**（本对话结束时此区域的最终状态）：

```css
    /* phone-row 在 mobile 改成单列布局 */
    .phone-list { grid-template-columns: 1fr; gap: 8px; padding-right: 0; }
```

---

## 对话时间：2026-08-05（发送到多维表改造 + 状态栏菜单修复 + 按钮样式统一）

## 改动概述：send_to_kinboard 改 AJAX JSON + 电话拆分为主电话/其他电话 + 说明弹窗 + 设置页折叠 + menubar 菜单改名 + 401 白名单修复

### 排查结论：✅ 全部未丢失

本次对话修改的 6 个文件逐一核验，当前文件内容均包含本对话的最终改动，**无任何改动丢失**。

具体核验清单：

1. **`routes/companies.py`** ✅ — `send_to_kinboard` 已改为 AJAX JSON 接口（`jsonify` 返回，非 `redirect`）；电话拆分为 `主电话`（`validate_phone` 校验 mobile）+ `其他电话`（所有号码含主号）；从 JSON body 读 `remark`。（注：后续会话在此基础上又加了 `follow_up` 和 `企业名称` 字段名，已在上方条目记录）

2. **`templates/company_detail.html`** ✅ — 发送按钮从 `<form>` 改为 `<button onclick="openKinboardModal()">`；`openKinboardModal` 函数存在；按钮行 `margin-top: 16px`（原 10px）；在线更新按钮从 `detail-refresh-btn` 改为 `page-btn`；发送到多维表按钮去掉内联 `style` 覆盖。（注：后续会话在 `openKinboardModal` 内增加了数据预览和跟进记录，已在上方条目记录）

3. **`templates/settings.html`** ✅ — 字段说明用 `<details>`/`<summary>` 默认折叠；字段列表已更新（删除 `电话号码`，新增 `主电话`/`其他电话`/`说明`）；JSON 示例已更新为 7 字段；`主电话` 描述含手机号限制说明；底部 chevron 旋转 JS 存在

4. **`app.py`** ✅ — `_PUBLIC_PATHS` 包含 `/api/phone_count_text` 和 `/api/quick-import/extract`

5. **`menubar.py`** ✅ — 菜单项为「一键标注」和「智能录入」（原「一键标注号码」「智能提取录入」）；`on_smart_extract` 和 `on_quick_annotate` 均有 `except urllib.error.HTTPError` 分支（在 `URLError` 之前）

6. **`CHANGELOG.md`** ✅ — 顶部有 `## 2026-08-05 — 发送到多维表改造 + 状态栏菜单修复` 条目

---

## 对话时间：2026-08-07
## 改动概述：微信图标三状态（灰未填/绿已填/橙无微信）+ 仅手机号显示微信 + 编辑弹窗简化为单行 + 移除"未登记邮箱" + api 返回 phone_type

### 排查结论：✅ 全部未丢失

本次对话修改的 4 个文件逐一核验，当前文件内容均包含本对话的最终改动。其中 `company_detail.html` 和 `style.css` 被后续会话进一步演进（增加了性别着色、粘贴按钮等），但本对话的基础改动均保留。`phones.html` 和 `api.py` 与本对话最终状态完全一致。

具体核验清单：

1. **`api.py`** ✅ — `validate_phone` 导入（第 16 行）；`phone_count` 返回 `phone_type`（第 831 行）；`phone_count_batch` 返回 `phone_type`（第 863 行）。完全一致。（注：第 171 行有后续会话添加的重复导入 `from utils import phone_location, ...validate_phone`，非本对话改动）

2. **`static/style.css`** ✅ — `.phone-location` 有 `white-space: nowrap`（第 1199 行，修复了此前 CSS 断裂）；`.phone-wechat-btn` 基础类存在；`.has-wechat`/`.no-wechat`/`.wechat-tip` 均存在。后续会话将 `.no-wechat` 从橙色改为灰色+删除线，并新增 `.wechat-male`(蓝)/`.wechat-female`(粉) 及 tooltip 按色着色规则。

3. **`templates/company_detail.html`** ✅ — Jinja 三状态判断（`cp.wechat.wechat_name != '无微信'` / `== '无微信'` / else）存在（第 299-317 行）；`{% if cp.phone_type in ['mobile', 'mobile_ext'] %}` 仅手机号显示微信；`markNoWechat`/`_doSaveWechat`/`refreshPhoneWechat` 函数均存在；"未登记邮箱" else 分支已移除（第 365 行直接 `{% endif %}`）。后续会话在弹窗中增加了性别按钮（＋男/＋女）、粘贴按钮、清空输入按钮和 `setWechatGender`/`pasteToWechat`/`clearWechatInput` 函数，并将 `savePhoneWechat` 空值行为从"提示不能为空"改为"清除记录"。

4. **`templates/phones.html`** ✅ — `single-wechat-row` 包裹层（display:none 初始隐藏）存在（第 96 行）；三状态显示逻辑（`无微信` 判断）存在（第 413-419 行）；`phone_type` mobile 判断存在（第 410 行）；重复 `const norm` 声明已修复（第 423-425 行不再重复声明）；`markNoWechatSingle`/`_doSaveWechatSingle` 函数均存在。与本对话最终状态完全一致，未被后续会话修改。

---

## 对话时间：2026-08-07
## 改动概述：企业录入/编辑表单卡片化 + 联系方式「重要」标签 + 电话校验空格分隔修复 + 详情页联系方式与地址位置交换 + 电话卡片高度不一致修复

### 排查结论：⚠️ 3 处改动丢失，1 个文件完整保留

本次对话修改了 3 个文件。其中 `templates/_company_form.html` 的全部改动均保留；`static/style.css` 和 `templates/company_detail.html` 的改动全部丢失。

**保留的文件：**
1. **`templates/_company_form.html`** ✅ — 5 个 section 均有 `detail-section--card` 类；联系方式标题有 `<span class="imp-badge">重要</span>`；电话校验 JS 有空格分隔逻辑（`_isValidPhone` 函数 + `finalParts` 空格拆分）。全部完好。

**丢失的改动（下方详述）：**
1. **`static/style.css`** ❌ — `.imp-badge` CSS 类丢失（联系方式「重要」标签无样式）
2. **`static/style.css`** ❌ — `.dg-body .phone-row` 的 `margin-bottom: 8px` 和 `:last-child` 覆盖仍存在（电话卡片高度不一致 bug 未修复）
3. **`templates/company_detail.html`** ❌ — 联系方式与地址位置交换丢失（地址仍在基本信息 section 内部，联系方式仍在地址之后）

---

### 文件：`/Users/gm/AI/EntHub/static/style.css`

**改动位置1**：`.req-badge` 样式之后（约第 638 行，`.name-check-result` 之前）

**改动说明**：
`_company_form.html` 中联系方式标题旁新增了 `<span class="imp-badge">重要</span>` 标签，需要在 CSS 中添加对应的样式类。样式与 `.req-badge`（必填标签）风格一致，但使用橙色/警告色而非红色/危险色，以区分「重要」与「必填」。

**最终代码**（本对话结束时此区域的最终状态）：

```css
/* 重要徽章（联系方式等关键字段） */
.imp-badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 600;
    color: var(--warning);
    background: var(--warning-light);
    padding: 1px 7px;
    border-radius: 8px;
    margin-left: 6px;
    vertical-align: middle;
}
```

---

### 文件：`/Users/gm/AI/EntHub/static/style.css`

**改动位置2**：`.dg-body .phone-row` 样式（约第 1095-1104 行，`/* 联系方式卡片内的 phone-row */` 注释下方）

**改动说明**：
修复电话卡片高度不一致的 bug。原 CSS 中 `.dg-body .phone-row` 有 `margin-bottom: 8px`，同时 `.dg-body .phone-row:last-child` 有 `margin-bottom: 0`。在 CSS Grid 中 `align-items: stretch`（默认值）会将 grid item 的 margin box 拉伸到行高，因此 border box 高度 = 行高 - margin。当同一行两张卡片 margin 不同时（因为 `:last-child` 覆盖），高度就会出现 8px 差异。表现为：2 个电话时主号矮、另一个高；3 个电话时高度一致。修复方式：删除 `margin-bottom` 和 `:last-child` 覆盖，grid 的 `gap: 10px` 已负责行间距。

**最终代码**（本对话结束时此区域的最终状态）：

```css
/* 联系方式卡片内的 phone-row：透明背景，仅靠边框和左侧色条区分 */
/* 注意：不要加 margin-bottom，grid 的 gap 已负责行间距；
   否则 :last-child margin-bottom:0 会导致同行两张卡片高度不一致 */
.dg-body .phone-row {
background: transparent;
border-radius: 8px;
border: 1px solid var(--border);
border-left: 3px solid var(--accent);
padding: 10px 14px;
}
```

---

### 文件：`/Users/gm/AI/EntHub/templates/company_detail.html`

**改动位置**：基本信息 section 和联系方式 section（约第 223-380 行）

**改动说明**：
将联系方式和地址的位置交换。原来地址在基本信息 section 内部（工商主体/资本/日期之后），联系方式在基本信息 section 之后。改为：基本信息 section 只含工商主体/资本/日期，联系方式紧随其后（在工商主体下方），地址作为独立 section 放在联系方式之后。

**最终代码**（本对话结束时此区域的最终状态）：

原来的结构（当前文件中的状态，即丢失后的状态）：
```html
<!-- 基本信息 section 内部：工商主体/资本/日期 之后直接跟 地址 -->
    </div>  <!-- detail-groups 结束 -->
    <!-- 地址 -->
    <div class="detail-groups dg-split dg-address">
      <!-- 行政区划 + 地址信息 -->
      ...
    </div>
  </section>  <!-- 基本信息 section 结束 -->

  <!-- 联系方式 -->
  <section class="detail-section">
    ...
  </section>
```

应改为（本对话的最终状态）：
```html
    </div>  <!-- detail-groups 结束 -->
  </section>  <!-- 基本信息 section 在此处关闭，不含地址 -->

  <!-- ─────────── 联系方式 ─────────── -->
  <section class="detail-section">
    <div class="detail-groups">
      <div class="detail-group dg-main">
        <div class="dg-title">联系方式</div>
        <div class="dg-body">
          ...电话...
          ...邮箱...
          ...网址...
        </div>
      </div>
    </div>
  </section>

  <!-- ─────────── 地址信息 ─────────── -->
  <section class="detail-section">
    <div class="detail-groups dg-split dg-address">
      <!-- 行政区划（左） -->
      <div class="detail-group dg-regcodes">
        <div class="dg-title">行政区划</div>
        <div class="dg-body">
          {% if company.province %}
          <div class="detail-field">
            <div class="detail-label">省</div>
            <div class="detail-value">{{ company.province }}</div>
          </div>
          {% endif %}
          {% if company.city %}
          <div class="detail-field">
            <div class="detail-label">市</div>
            <div class="detail-value">{{ company.city }}</div>
          </div>
          {% endif %}
          {% if company.district %}
          <div class="detail-field">
            <div class="detail-label">区/县</div>
            <div class="detail-value">{{ company.district }}</div>
          </div>
          {% endif %}
        </div>
      </div>
      <!-- 地址信息（右） -->
      <div class="detail-group dg-scope">
        <div class="dg-title">地址信息</div>
        <div class="dg-body">
          {% if company.address %}
          <div class="detail-field">
            <div class="detail-label">注册地址</div>
            <div class="detail-value">{{ company.address }}</div>
          </div>
          {% endif %}
          {% if company.annual_report_address %}
          <div class="detail-field">
            <div class="detail-label">最新年报地址</div>
            <div class="detail-value">{{ company.annual_report_address }}</div>
          </div>
          {% endif %}
          {% if company.mailing_address %}
          <div class="detail-field">
            <div class="detail-label">通信地址</div>
            <div class="detail-value">{{ company.mailing_address }}</div>
          </div>
          {% endif %}
        </div>
      </div>
    </div>
  </section>

  <!-- ─────────── 工商注册信息 + 经营范围（并排） ─────────── -->
  <section class="detail-section">
    ...
```

即：从基本信息 section 中移除地址部分（`detail-groups dg-split dg-address` 整块），在基本信息 `</section>` 后先放联系方式 section，再放地址 section，最后是工商注册信息 section。

---

## 对话时间：2026-08-07
## 改动概述：电话号码拖拽排序功能（detail 页 phone-row 拖拽 + sort_order + reorder API），共涉及 4 个文件

### 排查结论：❌ 全部丢失

本次对话实现的电话号码拖拽排序功能（区别于电话标记拖拽，后者存活）在回退中全部丢失。4 个文件的改动均不存在于当前代码中。

**存活的改动（无需恢复）：**
1. `templates/company_detail.html` — Hero重构、工商查询下拉、联系方式卡片化、微信编辑器(paste/clear/性别直接保存)、电话标记拖拽(initPhoneTagDrag) ✅
2. `static/style.css` — no-wechat::after 横线、dg-body phone-row 透明背景、卡片间距微调 ✅
3. `routes/phone_tags.py` — reorder_phone_tags API ✅

**丢失的改动（下方详述）：**
1. `db.py` — sort_order 列迁移 ❌（当前代码反而用 `_try_drop_column` 删除 sort_order）
2. `routes/companies.py` — reorder_phones API + sort_order 排序逻辑 ❌
3. `templates/company_detail.html` — phone-row draggable + grip-vertical 手柄 + initPhoneDrag JS ❌
4. `static/style.css` — .phone-drag-handle / .phone-row.dragging 样式 ❌

---

### 文件：`/Users/gm/AI/EntHub/db.py`

**改动位置**：`init_db()` 函数，`_migrate` 调用区域（约第 211-213 行）

**改动说明**：
为 `company_phones` 表添加 `sort_order INTEGER DEFAULT 0` 列，用于存储用户拖拽排序的自定义顺序。通过 `_migrate` 函数自动给已有数据库加列。

**最终代码**（本对话结束时此区域的最终状态）：

```python
    _migrate(conn, "companies", "normalized_legal_person", "TEXT")
    _migrate(conn, "company_shareholders", "position", "TEXT")
    _migrate(conn, "company_phones", "sort_order", "INTEGER DEFAULT 0")
```

⚠️ 注意：当前代码中 sort_order 不仅没有 migrate，反而被 `_try_drop_column(conn, "company_phones", "sort_order")` 主动删除。恢复时需要同时处理这个删除逻辑。

---

### 文件：`/Users/gm/AI/EntHub/routes/companies.py`

**改动位置1**：`company_detail` 函数，电话查询 SQL（约第 48-57 行）

**改动说明**：
SQL ORDER BY 改为 `CASE WHEN cp.sort_order > 0 THEN 0 ELSE 1 END, cp.sort_order, cp.is_primary DESC`，有自定义排序时按 sort_order，无则按原逻辑。

**最终代码**（本对话结束时此区域的最终状态）：

```python
    # 该公司的所有电话（含重复数），按用户自定义排序（sort_order 优先，无排序时按类型+重复数）
    company_phones = g.db.execute(
        """SELECT cp.phone, cp.normalized_phone, cp.is_primary,
               (SELECT COUNT(DISTINCT company_id)
                FROM company_phones cp2
                WHERE cp2.normalized_phone = cp.normalized_phone) AS dup_count
           FROM company_phones cp
           WHERE cp.company_id = ?
           ORDER BY CASE WHEN cp.sort_order > 0 THEN 0 ELSE 1 END, cp.sort_order, cp.is_primary DESC""",
        [company_id]
    ).fetchall()
```

---

**改动位置2**：`company_detail` 函数，Python 排序逻辑（约第 79-85 行）

**改动说明**：
只在没有任何 `sort_order > 0` 的记录时才做类型+重复数自动排序，否则保持 SQL 的 sort_order 序。

**最终代码**（本对话结束时此区域的最终状态）：

```python
    # 排序：如果用户已自定义排序（sort_order > 0），保持 SQL 的 sort_order 序；
    # 否则按类型+重复数自动排序
    _has_custom_order = any(c.get("sort_order") and c["sort_order"] > 0 for c in company_phones_enriched)
    if not _has_custom_order:
        _type_order = {"mobile": 0, "mobile_ext": 1, "toll_free": 2, "toll_free_ext": 3,
                       "landline": 4, "landline_ext": 5, "invalid": 6}
        company_phones_enriched.sort(
            key=lambda c: (_type_order.get(c["phone_type"], 9), c["dup_count"], not c["is_primary"])
        )
```

---

**改动位置3**：文件末尾，`send_to_kinboard` 函数之后（约第 1020-1046 行）

**改动说明**：
新增 `POST /api/company/<id>/phones/reorder` API，接收 `{phones: ["norm1", "norm2", ...]}`，按顺序写入 sort_order。

**最终代码**（本对话结束时此区域的最终状态）：

```python
# ── 电话拖拽排序 API ──────────────────────────────────────────────────────

@bp.route("/api/company/<int:company_id>/phones/reorder", methods=["POST"])
def reorder_phones(company_id):
    """拖拽排序电话号码。

    请求体 JSON: { "phones": ["normalized_phone_1", "normalized_phone_2", ...] }
    按 phones 数组顺序写入 sort_order（1, 2, 3...）。
    """
    data = request.get_json(silent=True) or {}
    phones = data.get("phones")
    if not phones or not isinstance(phones, list):
        return jsonify({"code": 1001, "message": "参数错误：缺少 phones 数组", "data": None}), 400

    for idx, norm_phone in enumerate(phones, start=1):
        g.db.execute(
            "UPDATE company_phones SET sort_order = ? WHERE company_id = ? AND normalized_phone = ?",
            [idx, company_id, norm_phone]
        )
    g.db.commit()
    return jsonify({"code": 0, "message": "排序已保存", "data": None})
```

---

### 文件：`/Users/gm/AI/EntHub/templates/company_detail.html`

**改动位置1**：电话列表区域（约第 285-288 行）

**改动说明**：
phone-row 加 `draggable="true"`，最前面加 `grip-vertical` 拖拽手柄图标。

**最终代码**（本对话结束时此区域的最终状态）：

```html
      <div class="phone-list" id="phoneList">
        {% for cp in company_phones %}
        <div class="phone-row" data-norm="{{ cp.normalized_phone }}" draggable="true">
          <i data-lucide="grip-vertical" class="phone-drag-handle" style="width: 14px; height: 14px; color: var(--text-muted); cursor: grab; flex-shrink: 0;"></i>
          <a href="{{ url_for('pages_bp.search', q=cp.phone) }}" class="phone-number">{{ cp.phone }}</a>
          ...
```

---

**改动位置2**：`<script>` 块末尾，`toggleSourceFiles` 之后（约第 1230-1284 行）

**改动说明**：
新增 `initPhoneDrag` IIFE，使用原生 HTML5 Drag and Drop API 实现 phone-row 拖拽排序，dragend 时 POST 新顺序到 `/api/company/<id>/phones/reorder`。

**最终代码**（本对话结束时此区域的最终状态）：

```javascript
// ── 电话拖拽排序 ──
(function initPhoneDrag() {
    const list = document.getElementById('phoneList');
    if (!list) return;
    let dragSrc = null;

    list.addEventListener('dragstart', function(e) {
        const row = e.target.closest('.phone-row');
        if (!row) return;
        dragSrc = row;
        row.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
    });

    list.addEventListener('dragend', function(e) {
        const row = e.target.closest('.phone-row');
        if (row) row.classList.remove('dragging');
        // 保存新顺序
        const phones = Array.from(list.querySelectorAll('.phone-row')).map(r => r.dataset.norm);
        fetch(`/api/company/${companyId}/phones/reorder`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phones: phones })
        }).then(r => r.json()).then(res => {
            if (res.code === 0) window._enthubToast('success', '排序已保存', '');
            else window._enthubToast('error', '排序保存失败', res.message || '');
        });
    });

    list.addEventListener('dragover', function(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        const after = getDragAfterElement(list, e.clientY);
        if (!dragSrc) return;
        if (after == null) {
            list.appendChild(dragSrc);
        } else if (after !== dragSrc) {
            list.insertBefore(dragSrc, after);
        }
    });

    function getDragAfterElement(container, y) {
        const els = Array.from(container.querySelectorAll('.phone-row:not(.dragging)'));
        let closest = null, closestOffset = -Infinity;
        for (const el of els) {
            const box = el.getBoundingClientRect();
            const offset = y - (box.top + box.height / 2);
            if (offset < 0 && offset > closestOffset) {
                closestOffset = offset;
                closest = el;
            }
        }
        return closest;
    }
})();
```

---

### 文件：`/Users/gm/AI/EntHub/static/style.css`

**改动位置**：`.dg-body .phone-row:last-child` 之后（约第 1105 行）

**改动说明**：
拖拽排序的视觉样式：拖拽手柄默认半透明、hover 加深；拖拽中的行半透明+虚线边框。

**最终代码**（本对话结束时此区域的最终状态）：

```css
/* 拖拽排序 */
.phone-drag-handle {
    cursor: grab;
    opacity: 0.4;
    transition: opacity 0.15s;
}
.phone-row:hover .phone-drag-handle {
    opacity: 0.7;
}
.phone-drag-handle:active {
    cursor: grabbing;
}
.phone-row.dragging {
    opacity: 0.4;
    border-style: dashed;
    border-left-color: var(--accent);
}
.phone-row:not(.dragging) {
    transition: border-color 0.15s;
}
```

---

## 对话时间：2026-08-07
## 改动概述：代码恢复执行 — 恢复 Session 3 + Session 6 丢失改动，跳过 Session 7

### 恢复决策

| 项目 | 决策 | 原因 |
|------|------|------|
| Session 7（电话拖拽排序） | ❌ 不恢复 | 与已存在的主号体系（is_primary）冲突，用户选择保持当前主号体系 |
| Session 3（Grid 布局 padding-right） | ✅ 恢复 | — |
| Session 6a（.imp-badge CSS） | ✅ 恢复 | — |
| Session 6b（.dg-body .phone-row margin-bottom 修复） | ✅ 恢复 | — |
| Session 6c（联系方式与地址位置交换） | ✅ 恢复 | — |

### 恢复详情

**1. `static/style.css` — Session 3：`.phone-list` padding-right 恢复**
- 桌面端 `.phone-list` 添加 `padding-right: 12px`（补偿上方三列布局间隙），更新注释
- 768px 断点 `.phone-list` 添加 `padding-right: 0`
- 480px 断点 `.phone-list` 添加 `padding-right: 0`
- ⚠️ 偏差：gap 保持 10px（未恢复为 12px），因为 10px 可能是后续会话的有意调整，用户选择保持

**2. `static/style.css` — Session 6a：`.imp-badge` CSS 类**
- 在 `.req-badge` 之后添加 `.imp-badge` 样式（橙色「重要」标签，使用 `--warning` / `--warning-light` 变量）

**3. `static/style.css` — Session 6b：`.dg-body .phone-row` margin-bottom 修复**
- 删除 `margin-bottom: 8px` 和 `.dg-body .phone-row:last-child { margin-bottom: 0; }`
- 添加说明注释（不要加 margin-bottom，grid 的 gap 已负责行间距）

**4. `templates/company_detail.html` — Session 6c：联系方式与地址位置交换**
- 将地址块（`detail-groups dg-split dg-address`）从基本信息 section 中移出
- 基本信息 section 现在只含工商主体/资本/日期
- 地址作为独立 section 放在联系方式之后、工商注册信息之前
- 最终顺序：基本信息 → 联系方式 → 地址信息 → 工商注册信息