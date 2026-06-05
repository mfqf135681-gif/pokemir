# 主题:性能与 live 转向(破 1Hz + 走出温室)

> 最近更新:2026-06-05 立
> 涉及讨论:1 份(2026-06-05 讨论 + 代码侦察,本文即正文,无 archive)
> 当前状态:**讨论 + 读码侦察快照,未实现(推断 + 离线 n=1,live 数未进)**。下一步 = Stage 0 三个数定盘。

关联:[[2026-06-01_95pct-constraint-solver-paradigm]]、[[主题-识别栈]]、[[主题-基础设施]]、[[主题-产品形态]]、[[主题-DDD架构]]。
关联记忆:[[digit-ocr-stack-recipe]]、[[constraint-solver-paradigm]]、[[data-reliability-50-70-percent]]。

---

## 索引(按时间倒序)

| 时间 | 子主题 | 状态 | 关键决策 | 原文 |
|:---|:---|:---|:---|:---|
| 2026-06-05 | live 转向 + tick 性能作战图(含参照差分闸门) | 讨论定向,待实现 | 走出温室 / 配方替 OCR 数字区 / 三杠杆破 1Hz / 闸门列 Stage 2 | 本文正文 |

---

## 当前结论

### §1 战略:走出温室

1. **离线夹具磨太久了**。夹具的活(验地基逻辑)基本做完;n=1 上继续抠 precision 是边际递减 + 舒适区。
2. **夹具结构上测不了封存死因**。回放喂【已存帧】,而 1Hz 是【实时采集+处理】瓶颈——录像把这维度抹掉了。录三段也没用,测错了东西。
3. **检验分两种,只做了一种**:逻辑/精度(离线,够了)vs **live 可行性(只能 live,没碰,存在性风险全在这)**。
4. **夹具角色翻转**:不是终点,是显微镜——以后录 live 实战帧,出问题拉回离线 replay-debug。
5. **真正没测的根本**:"画像对打牌有没有用"(产品核心价值假设),从没尝过这道菜。

### §2 封存死因 & 解法判断

- **死因(用户确认)**:tick 卡 1Hz,怎么优化都破不了。
- **根因**:EasyOCR **既是准度天花板**(~75% 且严重不准)**又是速度地板**(1Hz)——两头堵在同一块石头。100ms 抠是治症状没碰病因(这活不该用 EasyOCR)。
- **配方是钥匙**:对数字区又快又准,同一动作把两堵墙一起推倒,不是 tradeoff。
- **"复用老管线+新桩基"= 定点换发动机,不是重建**(前提:封存死因确是性能 → 已确认)。
- **双 OCR(注意力)为什么死**:精心调度错的工具,且和 §15"密集全读"对撞。教训:**发现自己在为昂贵操作设计配给系统时,先问"它非得这么贵吗"**。

### §3 代码侦察发现(标清:事实 / 读码推断)

**热路径架构(事实):**
- `main.py → pipeline/orchestrator.py`,**mss 截屏**(`capture/screen.py`),`_tick` 循环;`CAPTURE_INTERVAL_MS=250`(设计 4Hz),**周期 = tick 耗时 + 250ms sleep(额外加)**;卡 1Hz ⇒ tick 本身 ~750ms。
- **已自带体检仪**:每 tick per-phase 计时 + 打日志(`tick_ms`/`pct_of_tick`,`orchestrator.py:290-493`)——跑起来自报谁慢。注释留有"seat_actions ~410ms 未计时 gap"。
- **名字已 throttle**:`capture_ids` 每 8 tick 才读(`orchestrator.py:391`)——"名字移出热路径"老管线早做了。

**1Hz 真凶(读码推断,待实测):**
- 每座 OCR:`seat_stack/amount/action_ocr` × 8 座,每 tick 二十几次神经网络调用。
- 数字集成点干净:`orchestrator.py:2022-2092 / 1652-1686 / 1290-1299`(均 `self.ocr.read_text(stack_area/amount_area)`)。
- **动作词 OCR 不能顺手砍**:缠在 `detector.py:239 check_action_change` 触发器、且有平行 `events/normalizer.py:infer_action_from_delta`。数字能干净换,动作词缠着。
- **死复杂度**:`ATTENTION_MODE`/双 OCR/Phase1.5 默认全关(`config.py:43`)——绕开,将来删。

**参照差分 / 闸门现状(用户"基线对比"想法的实情):**
- 闸门脚手架**在**且 legacy 默认生效(`detector.py:195 is_skippable_seat`,`orchestrator.py:2079` 读前 skip 弃牌/空座)——但**粗**:整座粒度 + 手级作用域 + **活跃座零闸门**(活跃座每 tick 全读 stack/action/amount/fold)。
- **空桌 baseline JSON 是孤儿**:`tools/capture_empty_seat_baseline.py`(T18)写了它,**运行时无人加载**(当年断在半路)。
- 现有判空用退化代理(`_detect_empty_seats`:`phash=="0"×64` AND stack-OCR)——**连判空都还调了一次 OCR**,且只在手起始算一次。
- **无横幅/遮挡检测**。
- 现成可复用零件:`_avg_hash_64`、`white_frac`、孤儿 baseline JSON + 采集工具、skip 挂点。

### §4 三根性能杠杆(都指向 1Hz)

| 杠杆 | 作用 | 状态 |
|---|---|---|
| **A 换配方** | 每次读**更便宜** | 件已造好(`pipeline/digit_reader.py`,replay 验过) |
| **B 内容闸门(参照差分)** | 空区**根本不读** + 脏区**挡门外** | 半成品+孤儿,**待建**(用户想法,一等公民) |
| **C 变化闸门** | 上 tick 没变**不深处理** | 全新,概念最简单 |

> A = "读得便宜",B/C = "少读/不读"。**相乘才是破 1Hz 的完整拳。**

**杠杆 B 的两条用途(用户直觉的具现):**
- **B1 空 ROI → 跳过 OCR(速度)**:amount 区绝大多数 tick 对绝大多数座是空的却每 tick 被读 → 微秒级"空就跳"。砍掉的读可能比换配方还多。描述子用**墨占比/与参照均差**(比 phash 更便宜更准)。
- **B2 遮挡/横幅 → 拒读(精度)**:区域既不像"空"也不像"该有内容" = 外来物盖着(滚动公告横幅,一条亮带同扫一排座)→ 拒这批读。**廉价杀"真幻影"FP**(可能不必等求解器 #226)。phash 适合这类图案判定。
- **车道**:它是**在不在/什么状态**的探测器,**不是内容读取器**(`128`/`228` phash 近似,分不出数字)。它管闸门/路由,识别管读数。
- **坑**:亮度(弃牌变暗)/动画会骗它 → 需亮度归一 + 时序稳定(状态持续几帧才认);参照按桌皮/座采集。

### §5 落地路线(分阶段)

- **Stage 0 — 离线微基准(最先、最便宜、无温室)**。量三个数定盘:① 配方 vs EasyOCR 读同批 ROI 的 ms;② `seat_action_ocr` 占 tick 比;③ **amount/stack 每 tick 读空频率**(量杠杆 B 天花板)。
- **Stage 1 — 杠杆 A:换配方**。`POKEMIR_DIGIT_RECIPE` flag 守卫(默认关、一键回滚),init 按 profile 加载 `DigitReader`;数字区三处接"**配方为主 + EasyOCR 仅 None 兜底**";动作词不动。
- **Stage 2 — 杠杆 B:内容闸门(用户想法,本路线一等阶段)**。接回孤儿 baseline JSON(或改用墨占比参照);活跃座**逐区读前**加微秒级内容闸门(**第一刀 = `amount_area`**);flag 守卫(如 `POKEMIR_REGION_GATE`)可单开、可叠 A;B2 横幅检测为同层精度副产物。
- **Stage 3 — live 实测(真 test)**。A(+B)开,真桌跑几分钟,读自报 `tick_ms` → 破不破 1Hz;眼校配方在**真实渲染**下准度(n=1 录像≠live)。
- **Stage 4 —(若验成)再榨 + 清债**。杠杆 C 变化闸门;砍额外 250ms sleep;删死的 `ATTENTION_MODE`。

### §6 待拍板 & 诚实 caveat

**待拍板:** ① 数字读法:**配方为主+兜底**(荐)vs 纯替换;② 动作词 OCR:**本轮不碰、Stage 0 先量占比**(占比大才单开下一仗:封闭词表模板化 / 靠 stack 跌幅推动作)。

**caveat:** 全是**读码推断 + 离线 n=1**,live 数未进;**~410ms 未计时 gap** 可能非 OCR 成本(换 OCR/加闸门不一定全消);模板 / baseline **按桌皮**;配方 **live 渲染准度未验**。

---

## 关联记忆 / 待办

- **待办**:Stage 0 三个数(配方 vs OCR ms / action_ocr 占比 / 读空频率)→ 定盘 → 走出温室。
- **现成件**:`pipeline/digit_reader.py`(杠杆 A)、`tools/build_digit_templates.py`、`tools/probe_zero.py`、孤儿 `tools/capture_empty_seat_baseline.py`(杠杆 B 待接)。
- **关联记忆**:[[digit-ocr-stack-recipe]](配方)、[[constraint-solver-paradigm]](§15 stack-centric / 动作靠跌幅推)、[[data-reliability-50-70-percent]](95% 捕获主线)、[[hand-segmentation-corroboration]](离线夹具 recall 100% 金标准)。
