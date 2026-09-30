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

## 页面结构备忘（PlatformShell，2026-09-30 实测）

脚本断言按以下实测结构编写；页面再改版时同步更新本节：

- **菜单选择器**：带图标菜单项的可访问名是"图标 aria-label+文字"
  （`tool 数据库部署管理`），`getByRole('menuitem', {name})` 不可靠——
  统一用 `.platform-sidebar .ant-menu-title-content` 按文本定位（点击冒泡）。
  菜单树：`数据库部署管理`（叶子）→ `产品测试中心`（子菜单→产品子菜单
  `接入示例`/`FBase 数据库`/`fbasecman`→profile 叶子 `接入验证`/
  `多活回归测试`/`等保回归测试`/`fbasecman 回归测试`/`稳定性测试`）→
  `License 授权管理`（子菜单→`密钥管理`/`License 生成`）。
  **产品子菜单默认折叠**（products 异步加载，defaultOpenKeys 不生效），
  点叶子前先点产品名展开。
- **header title** `.header-title` 恒为当前页名；fbasecman 环境下部署页
  不渲染 `数据库部署管理` h3（产品 workspaceClass 接管）。
- **环境管理**在部署页：`新增环境`/`编辑当前环境` 按钮 + Segmented 切换条
  （窄视口横向滚动）；无独立"产品与环境"页，产品上下文由所选环境 product_id 决定。
- **部署向导**（fbasecman 环境）：`📐 部署向导` → `生成 pgcluster 回归部署方案`，
  必填 `data_root`/`license_file`/`mmr1_port`——默认值依赖 profile 接口
  **异步预取**，测试里显式填写避免竞态。
- **拓扑**：2D 为 GSAP/SVG（`DeploymentCanvas`），节点 `.topo-node[data-node-id]`
  （非 react-flow）；点击开节点 Drawer（`数据目录`、`启动节点`）。
- **测试页**（regress-console）：用例行 `div.case-row`，名称 `.case-name`
  （文本是 `c.name`，**target 在 title 属性**）；搜索框 placeholder
  `搜索用例名称、Core ID (如 CORE-13)、中文描述...`；`查看报告` 开
  `.regress-report-modal`（Tabs：交互步骤详情/检测项断言/架构拓扑看板/
  用例设计与配置/原始报告/运行时日志）。
- **环境数据噪音**：部署页对无部署配置的真实环境会打 console 404/422
  （`/environments/<id>/configuration|topology`）——属数据性失败，按
  `console.location().url` 过滤，勿计入 pageerror。

## 等保／多活报告验收（隔离数据）

不执行数据库用例。夹具服务在临时目录生成两套环境与归档结果：

```bash
# 终端 1，在项目根目录启动隔离 API（端口 18767，使用 frontend/dist）
.venv/bin/python frontend/tests/report-fixture.py
# 终端 2
cd frontend
PLATFORM_URL=http://127.0.0.1:18767 node tests/report-viewer.mjs
```

覆盖两个测试页的“查看报告”、步骤与实际结果、原始报告预览／下载、HTML 导出，并检查浏览器运行错误。
