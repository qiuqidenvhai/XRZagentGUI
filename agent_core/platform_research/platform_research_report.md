# AI平台聊天界面DOM结构调研报告

> 调研时间: 2026-06-28 08:44:36 | 共调研 6 个平台

---

## DeepSeek

- **URL**: `https://chat.deepseek.com`
- **加载状态**: ok
- **截图**: [deepseek_screenshot.png](screenshots/deepseek_screenshot.png)
- **body class**: `zh_CN light`
- **页面标题**: `DeepSeek - 探索未至之境`
- **当前URL**: `https://chat.deepseek.com/sign_in`
- **视口**: 1280x800

### iframe

- src: `https://open.weixin.qq.com/connect/qrconnect?appid=wx932d4fdaf46d5611&scope=snsapi_login&redirect_uri=https%3A%2F%2Fchat.deepseek.com%2Fapi%2Fv0%2Fusers%2Foauth%2Fwechat%2Fcallback&state=&login_type=jssdk&self_redirect=false&styletype=&sizetype=&bgcolor=&rst=&ts=1782607363007&stylelite=1&fast_login=0`

### 聊天输入框 (0 个)

**未找到标准输入框**


### 发送按钮 (1 个)

| # | 标签 | Class | 文字 | aria-label |
|---|------|-------|------|------------|
| 1 | `div` | `ds-button ds-button--primary ds-button--text ds-bu` | 发送验证码 |  |

### 文件上传 (0 个按钮, 0 个 input)

**未找到文件上传功能**

### 登录状态

- 无明确检测结果

> **Body HTML前500字符**: `<div id="root"><div class="c994dda2"><div class="_47c279e"></div><div class="_99ad066"><div class="ds-auth-form-wrapper ds-sign-in-form-wrapper"><div class="ds-sign-in-form__icon"><div><svg width="182" height="29" viewBox="0 0 182 29" fill="none" xmlns="http://www.w3.org/2000/svg" style="color: var(--dsw-alias-brand-primary);"><path d="M100.136 23.7767H98.1371V20.6775H100.136C101.374 20.6775 102.625 20.3688 103.431 19.5112C104.237 18.6535 104.542 17.3378 104.542 16.0229C104.542 14.708 104.25 13.`

---

## 通义千问

- **URL**: `https://tongyi.aliyun.com/qianwen/`
- **加载状态**: ok
- **截图**: [qianwen_screenshot.png](screenshots/qianwen_screenshot.png)
- **body class**: ``
- **页面标题**: `千问-阿里 AI 助手`
- **当前URL**: `https://www.qianwen.com/`
- **视口**: 1280x800

### iframe

- src: `https://passport.qianwen.com/havanaone/login/login.htm?bizPassParams=%26x-platform%3DexternalH5&bizEntrance=tongyi&bizName=tongyi&redirectType=topRedirect&lang=zh_CN&utdid=undefined&platform=web&appVersion=undefined&returnUrl=https%3A%2F%2Fwww.qianwen.com%2Fqianwen%2F`

### 聊天输入框 (3 个)

| # | 标签 | Class | Placeholder | aria-label | 可见 |
|---|------|-------|-------------|------------|-----|
| 1 | `[contenteditable]` | `relative min-h-[24px] w-full whitespace-pre-wrap break-words` |  |  | yes |
| 2 | `[contenteditable]` | `pointer-events-none absolute inset-x-0 top-0 select-none tex` |  |  | yes |
| 3 | `div[role=textbox]` | `relative min-h-[24px] w-full whitespace-pre-wrap break-words` |  |  | yes |

> Playwright额外发现 2 个 contenteditable

  1. className=`relative min-h-[24px] w-full whitespace-pre-wrap b` rect={'x': 398, 'y': 369.5, 'width': 734, 'height': 49.390625}
  2. className=`pointer-events-none absolute inset-x-0 top-0 selec` rect={'x': 406, 'y': 369.5, 'width': 718, 'height': 26}

> Playwright发现 1 个疑似发送按钮

  1. text=`` cls=`inline-flex size-8 shrink-0 items-center justify-c`

### 发送按钮 (1 个)

| # | 标签 | Class | 文字 | aria-label |
|---|------|-------|------|------------|
| 1 | `button` | `inline-flex size-8 shrink-0 items-center justify-c` |  | 发送消息 |

### 文件上传 (1 个按钮, 0 个 input)

| # | 标签 | aria-label | 文字 |
|---|------|------------|------|
| 1 | `button` | 添加附件 |  |

### 登录状态

- 无明确检测结果
---

## 豆包

- **URL**: `https://www.doubao.com/chat/`
- **加载状态**: ok
- **截图**: [doubao_screenshot.png](screenshots/doubao_screenshot.png)
- **body class**: ``
- **页面标题**: `豆包 - 字节跳动旗下 AI 智能助手`
- **当前URL**: `https://www.doubao.com/chat/`
- **视口**: 1280x800

### 聊天输入框 (1 个)

| # | 标签 | Class | Placeholder | aria-label | 可见 |
|---|------|-------|-------------|------------|-----|
| 1 | `textarea` | `semi-input-textarea semi-input-textarea-autosize` | 发消息... |  | yes |

> Playwright额外发现 2 个 textarea

  1. className=`semi-input-textarea semi-input-textarea-autosize` rect={'x': 400, 'y': 700, 'width': 760, 'height': 24}
  2. className=`` rect={'x': 520, 'y': 0, 'width': 760, 'height': 0}

### 发送按钮 (0 个)

**未找到明显发送按钮**


### 文件上传 (0 个按钮, 1 个 input)

- `<input type="file">` accept=.pdf, .txt, .csv, .docx, .doc, .xlsx, .xls, .pptx, .ppt, .md, .mobi, .epub, .png, .jpeg, .jpg, .webp, multiple=True

### 登录状态

- 无明确检测结果

> **Body HTML前500字符**: `<div id="root"><div id="chat-route-layout" class="w-full h-full flex overflow-hidden bg-s-color-bg-body" style="--content-max-width: 800px; --chat-area-max-width: 800px; --transition-duration-slow: 0.3s; --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1); --s-color-bg-mask: rgba(0, 0, 0, 0.3); --scrollbar-color: #ccc; --scrollbar-hover-color: #999; --input-guidance-input-caret-color: var(--s-color-accents-blue); --input-guidance-input-container-max-height: 300px; --input-guidance-input-container-min-he`

---

## 元宝

- **URL**: `https://yuanbao.tencent.com/chat/`
- **加载状态**: ok
- **截图**: [yuanbao_screenshot.png](screenshots/yuanbao_screenshot.png)
- **body class**: ``
- **页面标题**: `元宝-腾讯旗下全能AI助手`
- **当前URL**: `https://yuanbao.tencent.com/chat/naQivTmsDa`
- **视口**: 1280x800

### iframe

- src: `https://open.weixin.qq.com/connect/qrconnect?appid=wx12b75947931a04ec&scope=snsapi_login&redirect_uri=https%3A%2F%2Fyuanbao.tencent.com%2Fscan%3Fnonce%3DN6HZYJWyWHw3McaC&state=wechat_login&login_type=jssdk&self_redirect=false&styletype=&sizetype=&bgcolor=&rst=&ts=1782607427049&style=black&href=data:text/css;base64,LndlYl9xcmNvZGVfcGFuZWxfcXVpY2tfbG9naW4ge3BhZGRpbmctdG9wOiAyMHB4O30gLmltcG93ZXJCb3ggLnRpdGxlIHtkaXNwbGF5OiBub25lO30gLmltcG93ZXJCb3ggLnFyY29kZSB7bWFyZ2luLXRvcDogMDsgd2lkdGg6IDEwMCU7IGJvcmRlcjogMDsgdmVydGljYWwtYWxpZ246IHRvcDt9IC5pbXBvd2VyQm94IC5zdGF0dXMuc3RhdHVzX2Jyb3dzZXIge2Rpc3BsYXk6IG5vbmU7fSAuaW1wb3dlckJveCAuc3RhdHVzIHtwYWRkaW5nOiAwOyB0ZXh0LWFsaWduOiBjZW50ZXI7fSAuaW1wb3dlckJveCAuc3RhdHVzX2ljb24ge2Rpc3BsYXk6IGJsb2NrIWltcG9ydGFudDsgcG9zaXRpb246IGFic29sdXRlOyB0b3A6IDUwdnc7IGxlZnQ6IDUwdnc7IG1hcmdpbjogLTIycHggMCAwIC0yMnB4OyBiYWNrZ3JvdW5kLWNvbG9yOiAjZmZmOyBib3JkZXItcmFkaXVzOiAxMDAlfSAuaW1wb3dlckJveCAuc3RhdHVzX2ZhaWwgaDQge2NvbG9yOiAjZDU0OTQxO30gLmltcG93ZXJCb3ggLnN0YXR1c19zdWNjIGg0IHtjb2xvcjogIzIwQzU3RH0gLmltcG93ZXJCb3ggLnN0YXR1c190eHQgcCB7ZGlzcGxheTogbm9uZX0gLmltcG93ZXJCb3ggLmluZm8ge3dpZHRoOiAxMDAlfSAud2ViX3FyY29kZV9zd2l0Y2hfd3JwIHttYXJnaW4tdG9wOiAwfSAuc3RhdHVzX2ZhaWwgLnN0YXR1c190eHQge2ZvbnQtc2l6ZTogMTRweH0gLnFsb2dpbl91c2VyX25pY2tuYW1lLC5qc19xdWlja19sb2dpbl9uaWNrbmFtZSB7bWF4LXdpZHRoOjE2MHB4O292ZXJmbG93OmhpZGRlbjt0ZXh0LW92ZXJmbG93OmVsbGlwc2lzO2Rpc3BsYXk6LXdlYmtpdC1ib3g7LXdlYmtpdC1saW5lLWNsYW1wOjI7LXdlYmtpdC1ib3gtb3JpZW50OnZlcnRpY2FsO3dvcmQtYnJlYWs6YnJlYWstYWxsO2xpbmUtaGVpZ2h0OjIwcHg7bWF4LWhlaWdodDo0MHB4O21hcmdpbi1sZWZ0OmF1dG87bWFyZ2luLXJpZ2h0OmF1dG87fQ==`

### 聊天输入框 (1 个)

| # | 标签 | Class | Placeholder | aria-label | 可见 |
|---|------|-------|-------------|------------|-----|
| 1 | `[contenteditable]` | `ql-editor ql-blank` |  |  | yes |

> Playwright额外发现 1 个 contenteditable

  1. className=`ql-editor ql-blank` rect={'x': 309, 'y': 677, 'width': 921, 'height': 22}

### 发送按钮 (0 个)

**未找到明显发送按钮**


### 文件上传 (1 个按钮, 0 个 input)

| # | 标签 | aria-label | 文字 |
|---|------|------------|------|
| 1 | `span` |  |  |

### 登录状态

- **检测到登录态**
- 检测详情:
  - Avatar img: <DIV> .yb-common-nav__ft__avatar
  - Possible username: "未登录" in <DIV>

> **Body HTML前500字符**: `<div id="__next"><div id="app"><div class="yb-layout agent-layout layout-pc yb-layout--pc-container"><div class="yb-nav-fixed yb-nav-fixed--pc-ctx"><div class="yb-common-nav__trigger yb-common-nav__trigger--grey" data-desc="unfold"><span style="font-size:20px" class="yb-icon iconfont-yb icon-yb-ic_sidebar_20"></span></div><div class="yb-common-nav__trigger"><div><span class="yb-icon iconfont-yb icon-yb-ic_temporary_20" style="font-size: 20px;"></span></div></div></div><div class="yb-nav-mobile__`

---

## ChatGPT

- **URL**: `https://chatgpt.com/`
- **加载状态**: navigate_error: Page.goto: net::ERR_CONNECTION_TIMED_OUT at https://chatgpt.com/
Call log:
  - navigating to "https://chatgpt.com/", waiting until "domcontentloaded"

- **截图**: [chatgpt_screenshot.png](screenshots/chatgpt_screenshot.png)
- **body class**: ``
- **页面标题**: ``
- **当前URL**: `chrome-error://chromewebdata/`
- **视口**: 1280x800

### 聊天输入框 (0 个)

**未找到标准输入框**


### 发送按钮 (0 个)

**未找到明显发送按钮**


### 文件上传 (0 个按钮, 0 个 input)

**未找到文件上传功能**

### 登录状态

- 无明确检测结果
---

## Gemini

- **URL**: `https://gemini.google.com/app`
- **加载状态**: navigate_error: Page.goto: net::ERR_CONNECTION_TIMED_OUT at https://gemini.google.com/app
Call log:
  - navigating to "https://gemini.google.com/app", waiting until "
- **截图**: [gemini_screenshot.png](screenshots/gemini_screenshot.png)
- **body class**: ``
- **页面标题**: ``
- **当前URL**: `chrome-error://chromewebdata/`
- **视口**: 1280x800

### 聊天输入框 (0 个)

**未找到标准输入框**


### 发送按钮 (0 个)

**未找到明显发送按钮**


### 文件上传 (0 个按钮, 0 个 input)

**未找到文件上传功能**

### 登录状态

- 无明确检测结果

---

# 总结对比

| 平台 | 状态 | 输入框数 | 发送按钮 | 上传 | body class摘要 |
|------|------|---------|---------|------|----------------|
| DeepSeek | ok | 0 | 1 | 0 | `zh_CN light` |
| 通义千问 | ok | 3 | 1 | 1 | `` |
| 豆包 | ok | 1 | 0 | 0 | `` |
| 元宝 | ok | 1 | 0 | 1 | `` |
| ChatGPT | navigate_error: Page.goto: net::ERR_CONN | 0 | 0 | 0 | `` |
| Gemini | navigate_error: Page.goto: net::ERR_CONN | 0 | 0 | 0 | `` |
