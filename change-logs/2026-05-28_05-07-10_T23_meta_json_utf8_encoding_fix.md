# T23 meta.json UTF-8 encoding fix(治 find_screenshot 100% 失败真因)

- **完成时间**:2026-05-28 05:07 UTC(北京 13:07)
- **关联需求讨论**:无(快速 bug fix,纯 encoding 修复)
- **关联前次 change-log**:`change-logs/2026-05-28_04-44-19_T20_shutdown_race_fix.md`(同 session,但独立 bug)
- **触发红线**:无
- **无关红线已检查**:R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-8, R-9, R-10(10 条)

## 1. 任务概述

- **用户原始需求**:baseline 工具历次报告 `has_screenshot=False`(35/40 / 全 65/65 全 False),即使 `data/review/<hand_id>/` 目录存在且 meta.json event_id 跟 DB 一致。
- **涉及功能模块**:
  - `pipeline/orchestrator.py` 的 `_save_review_artifacts`(写 meta.json)
  - `tools/label_baseline.py` 的 `find_screenshot`(读 meta.json)
- **相邻任务**:本次 fix 解释了为什么之前 baseline 验证全部"凭 Text 字段判断"(86.2-87.5% 准确率被用户标注偏差污染)

## 2. 假设清单

1. **假设**:Python `open(..., "w")` 默认用 UTF-8
   - **出处**:跨平台 Python 行为预期
   - **现实**:Windows 上**默认用系统 locale**(cp936 for 简体中文),不是 UTF-8
2. **假设**:`json.dump(ensure_ascii=False)` 写中文是 UTF-8
   - **出处**:`ensure_ascii=False` 含义是保留非 ASCII 字符
   - **现实**:`ensure_ascii=False` 不影响文件 encoding,**file handle 的 encoding 才决定**

## 3. 文件变更清单

| 文件路径 | 变更类型 | 变更说明 |
|:---|:---|:---|
| `pipeline/orchestrator.py` | 修改 (line 82) | `open(... "w")` → `open(... "w", encoding="utf-8")`,新 meta.json 全 UTF-8 |
| `tools/label_baseline.py` | 修改 (find_screenshot, ~5 行) | 读 meta.json:try UTF-8 → fallback cp936(兼容历史文件) |

### 附带修复(5 分钟规则)

无。

## 4. 契约一致性检查

- 是否涉及 `contracts/` 变更:**否**
- meta.json 格式不变(JSON 结构 + 字段)
- 仅 file encoding 改:`json.dump` 输出字节序列从 cp936 改为 UTF-8

## 5. 红线合规动作

无红线触发。

## 6. 测试结果

- **验证路径**:**快速验证**(单文件改 1 行 + 单文件改 5 行,逻辑明确)
- **未跑 pytest** — encoding fix 不影响 pipeline 逻辑,单元测试覆盖不到
- **真验证靠用户**:Win 端 git pull + 重跑 baseline 单 event,看 verbose 输出是否 "[find_ss] 找到截图:..."

⚠️ [[dev-rule-validate-blind-spots]]:Linux 端 Python 默认 UTF-8,**无法复现 Windows cp936 行为**,fix 仅靠用户 Win 端真实测试。

## 7. 手动操作提醒

```
⚠️ 手动操作:
1. Win 桌面机 `git pull` 拿 T23 fix
2. 重跑 `python tools/label_baseline.py --n-screenshot 1 --n-override 0 --n-clean 0`
3. 预期看到:
   "[find_ss] 找到截图:..." 不再 "读 meta 失败:'utf-8' codec..."
   📸 截图:  D:\project\pokemir\data\review\...
4. 截图弹出(cv2 窗口 或 Win Photos)
5. 后续 baseline 真能凭图标注,不再凭 Text 循环
```

## 8. 潜在影响范围

- **新 meta.json**:UTF-8 编码(规范),baseline 工具直接可读
- **历史 meta.json**(cp936):baseline 工具走 cp936 fallback 仍可读
- **109 个 conf<0.7 截图历史数据**全部解锁,**baseline 重跑 ground truth 真值**
- **预期下轮 baseline**:有截图后,bet→check 4 个错可能消除(那些 case 用户凭 Text 错标,看图后会改对)

## 9. 违规标注

无违规。本次 task 严格 follow Router 4 步:
- ✅ 第一行 Mode 声明(REQ → DEV)
- ✅ 加载 rules-dev.md
- ✅ 红线核验(10 条全过)
- ✅ 末尾自检 + change-log 落档

---

## 任务完成自检 checklist

- ✅ `change-logs/2026-05-28_05-07-10_T23_meta_json_utf8_encoding_fix.md` 已保存
- ✅ 测试套件未跑(encoding fix 单元测试无意义,真验靠用户 Win 端)
- ✅ 触发红线 ID 显式标注:**无触发**(已逐条核验 R-1~R-10)
- N/A — 无独立 requirement-discussion(快速 fix,< 10 行)
- ✅ §11 模式漂移防护遵守(用户明示"开发模式" → DEV)
