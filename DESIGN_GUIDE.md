# EntHub 设计准则

本文档记录 EntHub 项目的设计语言和开发规范，确保后续开发保持一致性。

---

## 🎨 视觉设计

### 1. 弹窗组件规范

**尺寸规范：**
- 弹窗容器：`max-width: 420px`，`width: 90%`
- 最大高度：`max-height: 75vh`
- 圆角：`border-radius: 10px`
- 阴影：`box-shadow: 0 8px 24px rgba(0,0,0,0.15)`
- 背景遮罩：`rgba(0,0,0,0.4)` + `backdrop-filter: blur(2px)`

**字体规范：**
- 标题：`15px`，`font-weight: 600`
- 正文：`13px`，`line-height: 1.5`
- 辅助文字：`12px`，`color: var(--text-muted)`

**间距规范：**
- Header：`padding: 14px 18px`
- Body：`padding: 18px`
- Footer：`padding: 12px 18px`
- 元素间距：`gap: 10px`

**使用场景：**
- 所有确认操作使用 `showConfirm()`
- 所有输入操作使用 `showInput()`
- 所有自定义内容使用 `showModal()`
- **禁止使用**浏览器原生 `alert()`、`confirm()`、`prompt()`

### 2. 按钮规范

**主要按钮（btn-primary）：**
- 背景：`var(--accent)`
- 文字：白色
- 圆角：`6px`
- 内边距：`padding: 6px 12px`（小按钮）

**次要按钮（btn-secondary）：**
- 背景：`var(--bg-secondary)`
- 边框：`1px solid var(--border)`
- 文字：`var(--text-secondary)`

**危险按钮（btn-danger）：**
- 背景：`var(--danger)`
- 文字：白色
- 用于删除等危险操作

**标签按钮样式：**
- 背景：`${color}15`（15%透明度）
- 边框：`1.5px solid ${color}40`（40%透明度）
- 文字：`${color}`
- 圆角：`14px`
- 内边距：`padding: 6px 11px`
- 字体：`12px`
- 色块：`width: 8px; height: 8px; border-radius: 2px`

### 3. 表单控件规范

**输入框（form-input）：**
- 字体：`12px`
- 内边距：`padding: 6px 9px`
- 边框：`1px solid var(--border)`
- 圆角：`6px`

**下拉框（form-select）：**
- 字体：`12px`
- 内边距：`padding: 6px 8px`
- 宽度：根据内容自适应（如 `width: 100px`）

### 4. 颜色规范

**预设标签颜色（9种）：**
```javascript
const colors = [
    '#ef4444', // 红色
    '#f97316', // 橙色
    '#eab308', // 黄色
    '#22c55e', // 绿色
    '#06b6d4', // 青色
    '#3b82f6', // 蓝色（默认）
    '#8b5cf6', // 紫色
    '#ec4899', // 粉色
    '#6b7280'  // 灰色
];
```

**使用场景：**
- 标签颜色选择器统一使用这9种颜色
- 不使用 color picker，直接使用预设色块
- 色块尺寸：`width: 36px; height: 36px; border-radius: 6px`

---

## 📐 布局规范

### 1. 页面布局

**页面标题区域：**
```html
<div class="page-header">
    <div class="page-title">标题</div>
    <div class="page-subtitle">副标题</div>
</div>
```

**卡片容器：**
- 背景：白色或 `var(--bg-secondary)`
- 圆角：`8px`
- 内边距：`padding: 20px`
- 边框：`1px solid var(--border)`
- 间距：`margin-bottom: 24px`

### 2. 导航栏

**主导航项：**
- 浏览
- 标签
- 关联
- 录入
- 导入
- 更多（二级菜单）

**二级菜单项：**
- 备份
- 清理
- 重启

**图标尺寸：**
- 导航图标：`width:15px;height:15px`
- 菜单图标：`width:14px;height:14px`

### 3. 表格规范

**表头：**
- 背景：`var(--bg-secondary)`
- 字体：`13px`，`font-weight: 600`
- 内边距：`padding: 12px`

**表格行：**
- 内边距：`padding: 12px`
- 悬停效果：`background: var(--bg-hover)`
- 边框：`border-bottom: 1px solid var(--border)`

**分页组件：**
- 位置：表格下方
- 样式：统一使用 `pagination` 类
- 每页条数选择：`10/25/50/100/200`

---

## 🔧 交互规范

### 1. 弹窗使用规范

**强制规则：**
- ✅ **所有弹窗必须使用页面内弹窗**（_modal.html组件）
- ❌ **禁止使用浏览器原生弹窗**（alert/confirm/prompt）

**原因：**
- 原生弹窗样式无法定制，与系统风格不统一
- 原生弹窗会阻塞JavaScript执行
- 原生弹窗在不同浏览器中表现不一致
- 页面内弹窗可以提供更好的用户体验

**替换方案：**

| 原生方法 | 替换方法 | 使用场景 |
|---------|---------|---------|
| `alert('消息')` | `showConfirm('标题', '消息', () => {})` | 提示消息 |
| `confirm('消息')` | `showConfirm('标题', '消息', callback)` | 确认操作 |
| `prompt('消息')` | `showInput('标题', '消息', 'placeholder', callback)` | 输入内容 |

**表单确认替换：**
```html
<!-- ❌ 错误 -->
<form onsubmit="return confirm('确定要删除吗？')">

<!-- ✅ 正确 -->
<form onsubmit="return confirmSubmit(event, '删除确认', '确定要删除吗？')">
```

**链接确认替换：**
```html
<!-- ❌ 错误 -->
<a href="/delete" onclick="return confirm('确定要删除吗？')">

<!-- ✅ 正确 -->
<a href="/delete" onclick="return confirmLink(event, '/delete', '删除确认', '确定要删除吗？')">
```

**JavaScript中的确认：**
```javascript
// ❌ 错误
if (!confirm('确定要停止吗？')) return;
doStop();

// ✅ 正确
showConfirm('停止确认', '确定要停止吗？', () => {
    doStop();
});
```

**可用函数：**
- `showModal(title, content, buttons)` - 自定义弹窗
- `showConfirm(title, message, onConfirm)` - 确认弹窗
- `showInput(title, message, placeholder, onConfirm)` - 输入弹窗
- `confirmSubmit(event, title, message)` - 表单确认
- `confirmLink(event, url, title, message)` - 链接确认

### 2. 复制功能

**复制按钮样式：**
```html
<button class="copy-btn" onclick="copyToClipboard('内容', this)" title="复制">
    <i data-lucide="copy" style="width:12px;height:12px"></i>
</button>
```

**复制反馈：**
- 图标变为 ✓（`data-lucide="check"`）
- 颜色变为绿色（`var(--success)`）
- 1.5秒后恢复

**使用场景：**
- 公司名
- 电话号码
- 邮箱地址

### 2. 批量操作

**工具栏：**
- 背景：`var(--accent-light)`
- 内边距：`padding: 12px 16px`
- 圆角：`8px`
- 动态显示/隐藏

**操作流程：**
1. 选择企业（复选框）
2. 显示工具栏
3. 点击操作按钮
4. 弹窗确认
5. 执行操作
6. 刷新页面

**大批量操作确认：**
- 超过50条：需要二次确认
- 超过100条：显示警告信息
- 限制：一次最多1000条

### 3. 标签操作

**添加标签弹窗：**
```
┌─────────────────────────┐
│ 添加标签                │
├─────────────────────────┤
│ 选择要添加的标签：      │
│ [标签1] [标签2] [标签3] │
│                         │
│ ─────────────────────── │
│ 或创建新标签：          │
│ [名称] [颜色▼] [创建]   │
├─────────────────────────┤
│              [关闭]     │
└─────────────────────────┘
```

**标签显示样式：**
```html
<span style="display: inline-flex; align-items: center; gap: 4px; 
             padding: 4px 10px; background: ${color}20; 
             color: ${color}; border-radius: 12px; 
             font-size: 12px; font-weight: 500;">
    ${name}
    <button onclick="removeTag(${id})">×</button>
</span>
```

---

## 🎯 功能规范

### 1. 标签管理

**标签管理页面位置：**
- 主导航栏（不在二级菜单）
- 路由：`/tags`

**创建标签：**
- 输入标签名称
- 选择预设颜色（9种）
- 点击创建

**编辑标签：**
- 使用弹窗编辑
- 可修改名称和颜色

**删除标签：**
- 使用确认弹窗
- 提示：此操作会同时删除所有企业上的该标签

### 2. 企业标签

**详情页显示：**
- 位置：公司名称下方
- 样式：彩色标签徽章
- 操作：点击 × 删除

**添加标签：**
- 点击"添加标签"按钮
- 弹窗显示可用标签
- 可直接选择或创建新标签

### 3. 关联发现

**关联类型：**
- 电话关联
- 邮箱关联
- 法人关联
- 标签关联

**分组显示：**
- 每个分组显示关联值和关联数量
- 点击分组标题展开显示所有企业
- 展开后显示企业列表（待实现）

**企业链接：**
- 所有企业名称可点击
- 点击跳转到企业详情页
- 悬停显示下划线

---

## 🚀 开发规范

### 1. 版本管理

**版本更新时机：**
- 重要功能完成后
- 不要在每次小改动后都更新版本
- 可以在提交信息中记录改动，但不一定更新版本号

**提交信息格式：**
```
简短描述（如：优化弹窗样式）

详细改动：
- 改动1
- 改动2
- 改动3
```

### 2. 代码组织

**模板文件：**
- `_modal.html` - 全局弹窗组件
- `_relation_groups.html` - 关联分组组件
- `base.html` - 基础模板（包含导航栏、弹窗组件）

**JavaScript函数：**
- 全局函数放在 `base.html` 或 `_modal.html`
- 页面特定函数放在对应模板的 `{% block scripts %}` 中

**API规范：**
- RESTful风格
- 返回JSON格式：`{ success: true/false, message: "...", data: ... }`
- 错误处理：返回 `{ error: "错误信息" }`

### 3. 性能优化

**批量操作：**
- 限制一次最多1000条
- 使用事务提高性能
- 大批量操作分批次执行

**查询优化：**
- 使用索引
- 避免全表扫描
- 分页查询

**前端优化：**
- 使用HTMX局部刷新
- 避免整页刷新
- 懒加载大数据

---

## 📝 待改进功能

### 1. 关联发现页面

**需求：**
- 分组标题可点击展开
- 展开后显示所有企业列表
- 每个分组独立分页
- 分页选项（每页条数）

**技术方案：**
- 使用手风琴（Accordion）效果
- 每个分组独立加载数据
- 保持HTMX局部刷新

### 2. 其他优化

**可能的改进：**
- 标签使用统计页面
- 标签搜索和筛选
- 批量导出功能
- 数据对比功能

---

## 📚 参考

**技术栈：**
- 后端：Flask + SQLite
- 前端：Jinja2 + HTMX + Lucide Icons
- 样式：自定义CSS（基于CSS变量）

**相关文件：**
- `templates/_modal.html` - 弹窗组件
- `templates/base.html` - 基础模板
- `static/style.css` - 全局样式
- `app.py` - 后端路由和API

---

**最后更新：** 2026-07-17  
**维护者：** EntHub 开发团队
