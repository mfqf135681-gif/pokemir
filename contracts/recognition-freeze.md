# 识别层冻结契约(Recognition Freeze)— ACTIVE

> **状态:`active`**(2026-06-12 用户确认冻结 + 解冻保险条款)。
> **强制力**:`.agents/project-constraints.md` R-11(修改冻结范围内代码 → 阻塞,走 §4 解冻流程)。
> **语义**:改识别层从【默认允许】翻转为【默认禁止 + 举证解冻】。冻结可逆;回归排查烧掉的天数不可逆。

---

## 1. 为什么冻结(证据,2026-06-11~12 三场审计)

- **每动一刀都流过血**:stack 兜底抢跑(230 毒值类)/ allowlist 收窄(空 ROI 退化 2-3×)/
  first_char 切字(埋 s4 全盲雷,潜伏数周)/ 删 fold_ocr(三连环补闸)。模式:信号耦合密、
  回归面不可见,"顺手优化"的真实成本是事后追凶的天数。
- **边际收益已死**:残余缺口主体 = 大锅混战静默投入(overlay 不渲染,信息论上读不出);
  同一缺口求解器五行补回(142 案:底池缺口+栈缺口+唯一未记录者=三约束唯一解)。
- **体检已充分**:三场跨桌型审计、误差全归因、修复类零复发、两手怪兽锅(3028/2320)逐笔验尽。

## 2. 冻结范围(代码;按签名冻)

`pipeline/orchestrator.py`(识别/采集/闸门全部)· `pipeline/detector.py` · `pipeline/action_phash.py`
· `recognition/*` · `capture/*` 中被 live 路径调用的部分。

**明确豁免(不算解冻)**:
- **数据/配置运维**:补录动作锚、重框 ROI(`rois/*`)、重建数字模板、空桌基线——运维操作不触代码;
- `tools/*`(审计/诊断/标注工具)随便改;
- **解释层新建**:#226 求解器、#241 接缝比对器住新模块,消费识别层输出,不改它;
- 纯注释/日志措辞、紧急安全修复(R-1/R-2 触发类)。

## 3. 每桩不确定度种子(冻结时点基线,2026-06-11~12 三场)

| 桩 | 冻结时实测 | 备注 |
|---|---|---|
| 按钮 | 133/133、40/40、65/65 | 跨场全中 |
| 总底池 latch | 132/133、38/40、65/65 ≈99% | 每手恰一次 |
| 总底池数字 | 轨迹检查点逐笔吻合;>1000 异常 0 笔 | 非守恒锚,端点才是 |
| 端点栈 | 三手怪兽锅(3028/2320/216)逐笔零误差 | 唯一承重锚 |
| amount | 填充 704/704;盲注陈旧 0/92;正确率下界 ~95%(A/A′/A″+兜底殿后) | 残错≈单帧错读,带 suspect 指纹 |
| 动作 phash | 首字框+墨量闸+32 锚;类间 ≥82(阈 12);各座 0.6-2.1 动作/手无盲座 | 跨两场两桌型稳定 |
| fold | 861/861 活跃集救援承载;幽灵 fold 0(双 latch 压制) | |
| all-in 写库 | 25/25 过闸带金额(分层:即时 mark=实时档/手末闸=写库档) | broken 看护防假阳 |
| 玩家 ID | 手内漂移 0(首用冻结);纯数字新注册 0(护栏) | 存量垃圾名未清(待数据手术) |
| result 双信源 | 121/121 写入;+xx×端点一致率 87.6% | 分歧照存喂求解器 |
| **community(不冻)** | 第 4 张矛盾率 18-42%(按桌型) | P2:结算帧权威读盘 + #245 |

手级闭合(hand-closure)基线:71.1%(常规桌)/ 45-63%(狂野桌);分层 Q1-Q3 70-85%、Q4 25-45%。
**KPI 必须分层读**(锅大小四分位),总量数失真。

## 4. 解冻流程 + 保险条款

**常规解冻**:审计数据证明缺口归因识别层(非求解器可补类)→ 在本文件 §5 立 case(hand_id+
归因+所需信号)→ 用户确认 → 改 → 全量审计回归 → 回冻。

**🛡️ 解冻保险条款(2026-06-12 用户要求)**:#226 求解器开工后,**凡求解器解不平且归因到
识别层的手,自动成为解冻 case**(求解器输出 `unsolvable_reason=recognition` 即记入 §5,
无需另行申请)。**同类 case 积累 ≥3 → 该信号的修改权自动解冻**,修复后重审计回冻。
下游真实失败是识别层的终审法官——不靠再录十场来猜。

## 5. 解冻 case 登记簿

| 日期 | hand_id | 归因信号 | 现象 | 状态 |
|---|---|---|---|---|
| 2026-06-12 | bafca031 | action dedup(orchestrator `_process_seat_actions`) | dedup 键 `(player,street,action)` 不含金额 → 同街合法二次动作(limp跟4→面对加注再跟36,间隔4.78s<5s窗)被当重复吞;大锅多bet重灾。用户肉眼复盘+`action.dedup_skip`诊断坐实归因识别层 | **已解冻修复** 2026-06-12:键加金额维度(差≤2=重复读去/显著不同=合法二次动作留),check/fold恒None退回纯时间窗;7场景模拟+全测试套验回归后回冻 |
| 2026-06-13 | (全局) | event 落库缺座位号(orchestrator 4 个 raw_data 写点 + 手末) | action_events 只存 player_name 不存座位号,端点按座位存 → 求解器靠筹码值反推座位(两次读有差→失败=MAPPING_GAP,长局补不平最大头)。用户洞察:对账该以座位主键,名字退展示 | **已解冻修复** 2026-06-13:raw_data 记 seat_index(循环主键现成)+ 手末 seat_names(player_id_map 快照);**仅记已知数,零识别逻辑改动**;塞 raw_data 不碰表结构(避 R-4)。求解器优先用 seat_names 零反推,无则回退砖3。62 测试绿;⚠️ 历史数据走 fallback,实效需新录一局验 |
| 2026-06-14 | (全局) | 端点采集用错 reader(`_capture_seat_stacks`) | initial/final 端点(求解器**唯一承重锚**)用纯 EasyOCR(`_ocr_stack_chips`)而非已验~100% 的数字配方 —— 最关键的锚用着全项目最差 stack 读法。缺座率 init7.7/fin7.6/命名8.0(坐人读空非空座);all-in final=0 被 EasyOCR 丢 lone-0(#243 同坑)。用户揪地基盲区:端点也是 OCR、当天然真值有循环论证风险 | **已解冻修复** 2026-06-14:改用 `_stack_chips_recipe`(`_ocr_stack_chips` 的**严格超集**:配方开→DigitReader 主读+EasyOCR 兜底,配方关→内部 fallback 原路、零行为变化)。stash 对照验证零新增失败(3 既有失败=无模型环境)。⚠️ 治读法差缺座、不治动画遮挡/读爆;实效需新录一局对比 init/fin 座覆盖。**待补**:用独立基准(人工标真值)量端点数值准确率,终结"想当然可靠" |
| 2026-06-23 | 0150fc81 / 8380a532（24手） | action dedup 缺 None 处理(orchestrator `_process_seat_actions`) | `action_text`=phash动作词+金额OCR 拼接;跟注后金额 OCR 抖掉成 None（"跟注 42"→"跟注"）触发 check_action_change 重发 → 同窗口同(player,street,action)又来一笔 amount=None。06-12 加的金额维度只覆盖 None==None 与 \|Δ\|≤2，**漏 None-vs-真值** → 当合法二次动作放行 → trailing-null 幽灵 call/bet/raise 落库（DB 实测 24手/3天，每笔皆 `[真值,…,null]`）。live 人话流显 `?(待核)`，污染动作完整性/画像（不破守恒，None 不进筹码和）。用户 live 观测 + DB 坐实归因识别层 | **已解冻修复** 2026-06-23:dedup 补 `_amt is None and _last_amt is not None`（窗口内）→ 判重。真二次动作必带真金额走"显著不同"分支保留（06-12 案 4→36/34→580 单测验留）；check/fold 恒 None 走旧 None==None 分支不受影响。**审慎弃纯栈门**（首帧 stack OCR 滞后 overlay→误杀真跟注的回归风险）。回归:核心套 59 passed + dedup 新分支 7/7 单测 + 全套零新增失败(3 既有=缺EasyOCR/CNN env)。**✅ 实效已验**(125手 fix 后局):19 拦/3 漏(全 >5s 窗外)/真回归探针(差>2 dedup)=0 → 86% 削减零回归 |
| 2026-06-23(二) | (live)+ f520cb3f(横幅原案) | action dedup 缺栈门 + 窗口过窄(同函数,续前条) | ① 横幅滚动叠 amount 框→同座刷垃圾 call(金额乱飞但 stack_before==after 栈死不动),金额维度 \|Δ\|大被当合法二次动作放行→幽灵洪流(f520cb3f s3 15笔);② None 残尾 3 笔卡在 5.0-6.0s 窗外(原窗 5s)。归因:dedup 没用"栈是否动"这个真commit信号 | **已解冻修复** 2026-06-23:**统一一处 dedup 改**——(a)窗口 5→60s(dedup 每手清 detector:307 故天然手内、放宽不跨手误伤);(b)加**栈门二次抑制**:已发过同键(_last在=非首笔)+ `stack_before==stack_after`→判重不管金额(横幅垃圾栈冻被抓;**真二次动作必动栈→stack_frozen False→保留**;栈读None→fail-open不抑制)。**审慎点**:只作二次抑制→首笔真call永放行(避纯栈门首帧回归);首笔横幅幽灵每座仍漏1(带 amount-suspect 指纹)。回归:扩展单测 9/9(limp跟4→再跟36/加34→580 动栈保留、栈读空fail-open)+ 全套147 passed零新增。diag 加 `reason`/`stack_before/after` 供探针。⚠️ 实效需 Win 验横幅垃圾率↓ + 栈门 dedup 无吃真升级动作 |
| 2026-06-23(三) | (前瞻/阶段0,非单手 bug) | **采集时序**:端点锚+玩家ID 在按钮移动刻【单帧】抓(orchestrator `_tick`/`_end_current_hand`/`_start_new_hand`) | 端点+ID 抓在上手 overlay 未散的最脏时刻。实测:端点 recon 干净仅 31%(423手)、155 个"net涨"里 **152(98%)非圆整=读取假象**(真买入必圆整)、TempUser 2741次/2天;而总底池 latch→按钮间有 **5.05s 中位/≈70tick 干净窗**(414手稳定)。缺口归因【采集时序】非识别算法 | **申请待批**(详见 §5.1)。纯逻辑核 `pipeline/clean_window.py` + 9 单测已建(冻结豁免);接线碰冻结,待用户批 → 改 → Win soak 对真值回归 → 回冻 |

---

### 5.1 解冻申请详情:阶段0 干净窗捕获接线(2026-06-23)

> 这是首个**前瞻型**解冻 case(非"某手 bug 归因"),走 §4 常规流程:数据证明缺口归因采集层 → 用户确认 → 改 → 全量审计回归 → 回冻。设计依据见 `requirement-discussions/主题-主程序架构重构.md》《执行排程》阶段0`。

**① 归因(数据接地,非凭印象)**
- 端点锚(承重锚 B-21)与玩家ID 现在【按钮移动那一刻】**单帧**抓 —— 上手 overlay 未散的最脏时刻。
- 后果实测(近 2 天):端点 chip_reconstruction 干净率仅 **31%**(132/423);155 个"net 涨/rebuy"里 **152(98%)是非圆整值**(真买入必圆整 → 这些是**读取假象**,非真筹码流);TempUser 注册 **2741 次/2 天**(根因=按钮刻 id 区被 action overlay 污染)。
- 而总底池 latch(结算)→ 按钮移动 间有 **中位 5.05s / ≈70 tick 干净窗**(414 手,p10-p90 4.5-5.8s,稳定=WePoker 固定结算展示时长)。
- → 缺口归因**采集时序**(采集层),**非识别算法**;符合 §4 解冻条件。

**② 解冻范围(碰冻结的最小集)** —— 仅 `orchestrator.py` 手生命周期采集时序:
- `_tick` button_cut 块(per-tick 喂窗 + button 时 finalize 路由)
- `_end_current_hand`(final 端点来源)
- `_start_new_hand`(initial 端点 + ID 来源)
- **不改任何识别算法**(DigitReader/EasyOCR/phash/CNN 一律不动);只改"何时调用、用单帧还是窗内多帧共识"。

**③ 接线方案(绞杀式 + env 闸,一行回滚)**
1. orchestrator 持 `self._clean_window = CleanWindowCapture()`(`__init__`);
2. `_tick` 内 `_process_pot` 后:若 `_pot_label_latched`,调 `cw.tick(settled=True, stacks=_capture_seat_stacks(), ids=<窗内 id 读>)`(**仅窗内读**,成本落空闲期);
3. button_cut 时先 `res = cw.finalize()`;
4. `_end_current_hand`:`final = res.stacks if not res.fallback else _capture_seat_stacks()`(中位共识);
5. `_start_new_hand`:`initial = res.stacks if not res.fallback else _capture_seat_stacks()`;ID 共识 `res.ids` 作 player_id_map **种子喂既有入口**(经缓存锁 #2/B-27,**不绕过**);
6. `cw.reset()` 进下手;
7. **env 闸 `POKEMIR_CLEAN_WINDOW`(默认 0=旧行为,1=启用)** —— 一行回滚,同 `BUTTON_CUT`/`ALLIN_DEFER` 惯例。

**④ 与现有桩交互风险(Win soak 须验)**
- player_id_map 缓存锁(B-27/#2)+ 手内冻结(`_hand_id_lock`):窗内 ID 共识只作**新手种子**喂既有 `_capture_player_ids` 路径,不绕过锁;
- 无结算手 fallback(实测 ~11/705 fold 到底)**必须真退回单帧**(B 不变量不破);
- #226 reconstruct 的 net/winner/rake 算法**不变**,只换输入来源(单帧→窗内中位)。

**⑤ 回归方案**
- ✅ Linux 纯逻辑单测 **9/9**(`tests/test_clean_window.py`,已过);
- 🖥️ Win soak 对**真值**(依赖阶段-1 真值集):端点干净率 31%→目标、TempUser 率↓、无结算手不漏、**A/B 量出"时序占噪声多少"**(这一刀自带的副产品);
- 回滚:`POKEMIR_CLEAN_WINDOW=0` 一行退回旧行为。

**⑥ 状态:申请待批** —— 用户确认后才动冻结代码。在此之前仅纯逻辑核(冻结豁免)已落地,orchestrator 一行未改。
