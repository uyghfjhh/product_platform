# 浏览器验收脚本

Playwright 端到端冒烟脚本，验证前端页面与后端 API 的真实集成。
这些脚本**不参与** `npm run build`/`tsc`，按需手动运行。

## 前置要求

```bash
# 1. 一次性安装浏览器（下载到 ~/.cache/ms-playwright，无需 root）
cd frontend
npx playwright install chromium

# 2. 系统依赖：chromium 需要 libgbm 等宿主库
#    Debian/Ubuntu:  sudo npx playwright install-deps chromium
#    无 sudo 环境:   浏览器将无法启动，脚本不可运行（环境约束，非脚本问题）
```

## 启动被测服务

```bash
# 终端 1：平台 API + 前端静态页（frontend/dist 需已构建：npm run build）
PYTHONPATH=backend .venv/bin/python -m platform_app.cli start \
    --host 127.0.0.1 --port 8766
```

## 运行

```bash
cd frontend
PLATFORM_URL=http://127.0.0.1:8766 node tests/browser-smoke.mjs
PLATFORM_URL=http://127.0.0.1:8766 node tests/license-management.mjs
```

| 脚本 | 覆盖 |
|---|---|
| `browser-smoke.mjs` | 工作台加载、环境创建/选择、API 联动（`npm run test:browser`） |
| `license-management.mjs` | License 管理页：生成密钥/修改口令/删除版本按钮与指纹列 |
| `scene-replay.mjs` | 场景画布回放：注入 scene.* 事件驱动拓扑/实体渲染 |
| `scene-live.mjs` | 场景画布实时事件流（SSE 推送路径） |
| `test_web_ui.mjs` | 早期通用页面巡检（已被上述专项脚本覆盖） |

脚本约定：通过 `PLATFORM_URL` 指定服务；断言页面角色元素（`getByRole`）
与 API 响应，页面 JS 错误（`pageerror`）会导致失败。
