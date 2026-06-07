# #240 补丁:phash 动作丢金额 + 加 diff 缓存提速(Win 实测发现)

- **完成时间**:2026-06-07 15:00
- **关联前次 change-log**:`change-logs/2026-06-07_14-35-39_240_action_phash.md`(续接型,#240 实装的 Win 实测修正)
- **关联需求讨论**:`requirement-discussions/2026-06-07_action-recognition-text-shape-phash.md`
- **触发红线**:无;**无关红线 R-1~R-10 已检查**(Image-only 仍合规)。

## 1. 任务概述
Win 8 人观战实测 #240:phash **识别在工作**(call 被认出,action_events 有该动作),但暴露两问题——
① **phash 认出的 call/raise/bet 全 amount=null**;② **`seat_action_ocr` 没掉(154-222ms)**。

## 2. 根因 + 修
| 问题 | 根因 | 修 |
|:---|:---|:---|
| **phash 动作丢金额** | 金额拼接整段在 `if action_text is None:`(OCR 块)内;phash 先设了 action_text → OCR 块连金额拼接一起被跳过 | 金额拼接**移出 OCR 块**到 seat-loop 体层,phash/OCR 两路共用;加 `_need_action_read`(=进入时 action_text 为 None)守门,排除 fold-early(不给 fold 读金额) |
| **seat_action_ocr 没掉** | phash 块每 tick 对所有活跃座 capture+match,**无 diff 缓存**;旧 OCR 路有 T52 diff 缓存跳过未变座 | phash 块加**同款 diff 缓存**(`cv2.absdiff < _DIFF_THRESHOLD` 且非 force-refresh → 复用上次 action_text;否则 match;复用 `_last_roi_img/_text/_roi_force_refresh_at`)。动作持久态→多数 tick 不重算 |

## 3. 文件变更
| 文件 | 变更 |
|:---|:---|
| `pipeline/orchestrator.py` | ① 金额拼接移出 OCR 块 + `_need_action_read` 守门;② phash 块加 T52 同款 diff 缓存(mirror `_capture_with_diff_trigger`) |

## 4. 契约一致性
- 涉及 `contracts/`:**否**。

## 5. 红线合规
- 无触发。

## 6. 测试结果
- 验证路径:**快速验证**(单文件,逻辑修正,接入路径 Win 验)。
- `py_compile` ✅ · `pipeline.orchestrator` import ✅ · 现有套件 23 passed ✅。
- 依赖属性核实存在:`_DIFF_THRESHOLD=3.0`(orchestrator:1921)· `_roi_force_refresh_at`(detector:89)。
- ⚠️ **Win owed**:重跑验 ① phash 动作 amount 不再 null;② `seat_action_ocr` 掉到 ~微秒级(diff 缓存命中)+ tick Hz↑;③ 漏读率(单参考)。

## 7. 手动操作提醒
⚠️ Win 重跑(参考已建,直接开 flag):
1. `$env:POKEMIR_ACTION_PHASH_LIVE="1"` → `python main.py pipeline --profile party_poker_8`(8 人观战)
2. 我 MCP 读 action_events(amount 不为 null)+ per-phase(seat_action_ocr↓)

## 8. 潜在影响范围
- 金额拼接移出:对 fold-early 不再触发金额读(`_need_action_read` 守门)——行为更对(原 fold 也不读,等价)。
- diff 缓存:phash 复用未变结果;force-refresh 每 4 tick 兜 stale;与 OCR 路同机制。

## 9. 违规标注
无。
