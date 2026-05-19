# 迁移说明：v0.1（pokemir 自有约束体系）→ v0.2.1（通用核）

**迁移日期**：2026-05-17

## 一、变更概述

本项目原约束体系（3 文件 router 风格）已迁移到**跨项目通用核 v0.2.1**：
- 4 模式（REQ / DEV / TEST / **DOC** 新增）
- 通俗化约束抽出为独立 `communication.md`
- 红线 ID 化（R-1 ~ R-7），可机械引用
- 项目特化数据全部下沉到 `project-constraints.md` + `project-glossary.md`
- Router 四步流程**显性输出**（不再仅靠 AI 自觉）

## 二、旧文件归档位置

旧的 v0.1 文件全部保留在 `.agents/_archived_pre_v0.2.1/`：

```
.agents/_archived_pre_v0.2.1/
├── AGENTS.md                # 原 pokemir 路由器
├── rules-req.md             # 原 pokemir 需求讨论规则
├── rules-dev.md             # 原 pokemir 开发规则
├── rules-test.md            # 原 pokemir 测试规则
├── agents-archived/         # 原 .agents-archived/ 整目录
└── agents-full/             # 原 .agents-full/ 整目录
```

## 三、红线 ID 与老约束的映射

| 新红线 ID | 老约束位置 | 状态 |
|:---|:---|:---|
| R-1 | 旧 rules-dev.md §6 "禁止未声明的 API 调用" | ✅ 迁移 |
| R-2 | 旧 rules-dev.md §6 "禁止未声明的数据存储" | ✅ 迁移 |
| R-3 | 旧 rules-dev.md §9 "修改 models.sql 同步 storage/models.py" | ✅ 升格为独立红线 |
| R-4 | 旧 rules-dev.md §5 "测试套件必须全通过" | ✅ 迁移（含 skip vs fail 说明）|
| R-5 | 旧 rules-dev.md §4 "保留历史代码" | ✅ 迁移 |
| R-6 | 旧 rules-dev.md §9 "ROI 配置变更" | ✅ 升格为独立红线 |
| R-7 | 旧 rules-dev.md §9 "识别模型 / Vision client" | ✅ 升格为独立红线 |

**老规则中已不在新红线中的内容**：
- 旧 rules-test.md §8 "项目级已知限制"（测试环境受限、平台耦合、数据库依赖、多用户场景）—— 这些是项目阶段性事实而非永久红线，已在 `project-constraints.md` 红线描述（R-4 注明 skip vs fail）中部分吸收，其余作为隐式知识保留在归档 rules-test.md

## 四、Router 四步对老用户的差异

| 维度 | v0.1 | v0.2.1 |
|:---|:---|:---|
| Mode 标签 | 隐式标注 | **显性输出**第一行 |
| 加载文件 | 不告知 | **显性输出**第二行 |
| 红线核验 | 散落在 rules-dev §4-9 | **显性输出**第三行：逐条 ID |
| 自检 | 文末提示 | **显性打印**第四步 checklist |
| Mode 切换 | 隐式 | **显性宣告** |
| 复合指令 | 自由格式 | **标准化宣告句** |

## 五、新增能力

1. **DOC 模式**：改文档不再走 DEV + change-log 重流程
2. **5 分钟规则**（rules-dev §4.1）：允许顺手修 ≤ 3 处错别字 / 未使用 import
3. **同主题 change-log 查重**（rules-dev §2.0）：DEV 启动前一律先 grep
4. **DOC 语义安全阀**（rules-doc §2 + §4）：进入前显性判定"仅文字 / 触及语义"

## 六、回滚方案

如有严重不适应：
1. 把 `.agents/_archived_pre_v0.2.1/` 下的文件移回原位置
2. 删除新写入的通用核文件
3. 在 git 历史中找到 v0.2.1 落地前的提交点
