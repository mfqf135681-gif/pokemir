# 完整 Agent 治理体系（已归档）

## 这是什么

从上一项目中抽象出的多模式 Agent 治理体系，包含：

| 文件 | 用途 |
|:---|:---|
| `AGENTS.md` | 意图路由器（REQ / DEV / TEST 三模式分流） |
| `rules-req.md` | 需求讨论模式（5 阶段工作流 + 通俗化输出规范 + 收敛期治理） |
| `rules-dev.md` | 开发模式（契约驱动 + 变更日志 + 测试门禁） |
| `rules-test.md` | 测试诊断模式（5 阶段诊断 + 报告持久化） |

## 什么时候该激活

以下 5 个信号中，任意满足 3 条即可考虑激活：

1. 项目文件数超过 40 个
2. 存在 4 个以上独立功能模块
3. 用户频繁提出"改动 A 会不会影响 B"类跨模块问题
4. 项目中已出现 `contracts/` 或 `docs/` 目录
5. 用户某次请求中同时涉及 5+ 个文件的修改

## 如何激活

1. 将本目录下的 `AGENTS.md` 覆盖到项目根目录
2. 将 `rules-req.md`、`rules-dev.md`、`rules-test.md` 放回 `.agents/` 目录
3. 删除或归档根目录当前的精简版 `AGENTS.md` 和 `.agents/rules.md`
4. **执行全局占位符替换**（见下方占位符清单），将所有 `{{PLACEHOLDER}}` 替换为项目实际路径和工具名

## 占位符清单

激活时必须将所有 `{{PLACEHOLDER}}` 替换为项目的实际值。以下是完整清单：

### 目录路径

| 占位符 | 说明 | 旧项目参考值 |
|:---|:---|:---|
| `{{CONTRACTS_DIR}}` | 契约文件目录 | `contracts/` |
| `{{DOCS_DIR}}` | 系统文档目录 | `docs/` |
| `{{REQUIREMENT_DISCUSSIONS_DIR}}` | 需求讨论记录目录 | `requirement-discussions/` |
| `{{CHANGE_LOGS_DIR}}` | 变更日志目录 | `change-logs/` |
| `{{TEST_REPORTS_DIR}}` | 测试报告目录 | `test-reports/` |
| `{{TESTS_DIR}}` | 测试套件目录 | `tests/` |

### 契约文件

| 占位符 | 说明 | 旧项目参考值 |
|:---|:---|:---|
| `{{API_CONTRACT}}` | API 契约文件路径 | `contracts/api.yaml` |
| `{{DATA_MODEL_CONTRACT}}` | 数据模型契约文件路径 | `contracts/models.json` |
| `{{DESIGN_GUIDELINES}}` | UI 设计规范文件路径（可选） | `docs/design-guidelines.md` |

### 工具与命令

| 占位符 | 说明 | 旧项目参考值 |
|:---|:---|:---|
| `{{TEST_CMD}}` | 完整测试套件执行命令 | 微信开发者工具 Node.js 运行 `tests/api.test.js` 等 |
| `{{MP_NAVIGATE_TOOL}}` | 页面导航自动化工具 | `mcp_weapp-dev_mp_navigate` |
| `{{MP_INPUT_TOOL}}` | 元素输入自动化工具 | `mcp_weapp-dev_element_input` |
| `{{MP_GET_LOGS_TOOL}}` | 日志获取自动化工具 | `mcp_weapp-dev_mp_getLogs` |
| `{{SCREENSHOT_TOOL}}` | 截图工具 | `activate_session_management_and_screenshot_tools` |

### 可选模块

| 位置 | 说明 | 默认状态 |
|:---|:---|:---|
| `rules-req.md` §4 收敛期治理规则 | 项目进入稳定迭代期、需要分层治理时启用 | 关闭 |
| `rules-dev.md` §4 共享模块同步规则 | 项目存在多处复制的共享代码目录时启用 | 自动忽略 |
| `rules-test.md` §8 已知限制与默认策略 | 按项目实际情况填写 | 占位 |

## 注意事项

- 激活前确认项目已进入稳定迭代期，有相对稳定的契约 / 文档基础
- **必须完成占位符替换再使用**——未替换的文件包含 `{{PLACEHOLDER}}` 语法，AI 无法正确解析
- 对于尚未建立的目录（如 `contracts/`），可在激活后逐步创建，相应占位符先指向计划路径即可
- `rules-req.md` §4 收敛期治理建议项目有明确的数据不变量基线后再开启，过早开启会阻碍迭代速度
