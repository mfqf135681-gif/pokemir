# 项目术语口语化词表（pokemir / poker 桌面识别系统）

> 由 AGENTS.md 通俗化约束（详见 `.agents/communication.md §1.2`）引用。
>
> **作用**：术语首次出现时，AI 不用即兴造口语解释，直接查表，保证跨任务一致性。

---

## 业务概念

| 术语 | 6-15 字口语解释 | 备注 |
|:---|:---|:---|
| poker 客户端 | 扑克游戏的桌面应用程序 | 本项目识别的对象 |
| 社区牌 | 桌面上公共可见的牌 | flop / turn / river |
| 玩家手牌 | 玩家自己持有的两张牌 | hole cards |
| ROI | 屏幕上要识别的特定区域 | Region of Interest，触发 R-6 |
| profile | 一组 ROI 配置的命名集合 | 通过 `tools/roi_config.py --name` 管理 |
| 合成图 | 用于测试的人工生成图像 | 真实客户端不可用时的替代验证 |
| 牌识别 | 通过视觉算法识别牌面的过程 | recognition pipeline 的核心 |

## 技术架构

| 术语 | 6-15 字口语解释 | 备注 |
|:---|:---|:---|
| pipeline | 从截图到识别到入库的完整处理链 | `python main.py pipeline` 入口 |
| capture | 抓取屏幕指定区域的图像 | `capture/` 模块 |
| recognition | 从图像识别出牌面数字花色 | `recognition/` 模块 |
| storage | 把识别结果持久化到数据库 | `storage/` 模块，依赖 PostgreSQL |
| Vision client | 调用视觉识别 API 的客户端 | 触发 R-7 |
| HF endpoint | HuggingFace 模型推理端点 | `HF_ENDPOINT` 环境变量 |
| 契约 | API 与数据结构的约定文件 | `contracts/api.yaml` + `contracts/models.sql`，触发 R-1/R-2 |
| ORM | Python 对象映射数据库表的层 | `storage/models.py`，触发 R-3 |

## 流程与状态

| 术语 | 6-15 字口语解释 | 备注 |
|:---|:---|:---|
| skip | 测试用例因依赖不可达而跳过 | 不算 fail，不触发 R-4 |
| fail | 测试用例真实失败 | 触发 R-4，阻塞 |
| DEBUG 模式 | 详细日志输出模式 | `POKEMIR_LOG_LEVEL=DEBUG` |
| 离线分析 | 用预录截图/合成图做识别测试 | 真实 poker 客户端不可用时用 |

---

## 收纳标准

| ✅ 应当入表 | ❌ 不应入表 |
|:---|:---|
| 项目专属术语 | 通用编程概念（`闭包` / `递归`） |
| 用户可能不熟悉的业务概念 | 用户已在 README / docs 中反复看到 |
| 容易混淆的同名概念 | 单字短词 / 拼写明显的术语 |
| 涉及红线触发的关键字 | 通过查 IDE 就能立即理解的 API 名 |

**收纳上限建议**：≤ 50 条。
