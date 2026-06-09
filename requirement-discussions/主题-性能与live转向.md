# 主题:性能与 live 转向(破 1Hz + 走出温室)

> 最近更新:2026-06-09 **提速落地 §0′**(FRAME_CAPTURE 默认开 + sleep 250→30 → 典型 ~5.6Hz;Stage0 实测逐ROI抓屏=36%tick;DB 已非瓶颈;瓶颈交棒 recognition)
> 涉及讨论:2026-06-05 讨论+侦察 → 2026-06-06 DB瓶颈落地 → 2026-06-09 提速落地(本文即正文,无 archive)
> 当前状态:**提速已落地**。tick 典型 **~180ms / 5.6Hz**(从 1.8Hz);逐ROI截屏 154ms(36%)→ 整帧一次 12ms;**真瓶颈现在是 recognition(`fold_ocr` ~108ms)= 杠杆 B / all-in #243**。
> **校验来源标注**:`(官方)`=context7 官方文档证实;`(实测)`=本仓库 A/B;`(代码)`=读源码事实;`(博客/待验)`=网络博客,中等可信,需本地实测。

关联:[[2026-06-01_95pct-constraint-solver-paradigm]]、[[主题-识别栈]]、[[主题-基础设施]]、[[主题-产品形态]]、[[主题-DDD架构]]。
关联记忆:[[digit-ocr-stack-recipe]]、[[constraint-solver-paradigm]]、[[data-reliability-50-70-percent]]。

---

## 索引(按时间倒序)

| 时间 | 子主题 | 状态 | 关键决策 | 原文 |
|:---|:---|:---|:---|:---|
| **2026-06-09** | **提速落地:Stage0实测 + 杠杆D兑现 + 瓶颈交棒** | **已落地** | capture_grab探针:逐ROI抓屏=36%tick(单项最大);FRAME_CAPTURE字节级验过→默认开(154→12ms);sleep 250→30(1小时软泡无回归);DB实测0.1ms已非瓶颈;瓶颈转recognition(fold_ocr 108ms=#243);VRAM压测10G/37min零OOM→GPU显存够 | §0′ |
| **2026-06-06** | **实测收口:DB 网络=真瓶颈 → 本地库** | **已落地** | 杠杆A(配方读stack)+ 杠杆D(整帧抓)上;细计时揪出 gap=每动作同步DB写(Win→VPS 197ms RTT);DB 迁 Win 本地 PG18 → persist 354→0.5ms、tick 翻 2-4× | §0 |
| 2026-06-05 | live 转向 + tick 性能作战图(含参照差分闸门) | 讨论定向 | 走出温室 / 配方替 OCR 数字区 / 四杠杆破 1Hz / 闸门 Stage 2 | §1-6 |

---

## 当前结论

### §0′ 提速落地(2026-06-09)⭐ 杠杆 D 兑现 + 瓶颈交棒 recognition

承 §0(DB 已治)继续。本轮把 Stage 0 做完、杠杆 D 兑现、瓶颈推到 recognition:

- **Stage 0(全 tick 拆解)做完**:加 `capture_grab` 子探针(`screen.py` 累计逐区 grab + orchestrator tick 头尾取 delta)→ 实测 **逐 ROI mss grab = 154ms = 36% tick(单项最大)**;`seat_actions 241 ≈ fold_ocr 149 + action_ocr 91`(**大半是抓屏不是识别**);**DB = 0.1ms(已非瓶颈,§0 本地库迁移生效)**;phash(#240)已 live(action 识别已便宜;`[OCR seat]` 那条日志是**过时错标签**,phash 结果也走它打印)。
- **杠杆 D(FRAME_CAPTURE)兑现 + 默认开**:`tools/verify_frame_capture.py` 真窗口(同 live `find_window_by_title`)+ profile 全 108 个 ROI **字节级一致**(空桌静止,负坐标副屏 + 1454×1287 锁分辨率都对齐)→ **纯提速、不改任何识别结果**。开后 `capture_grab 154→0`、`capture_frame=12ms`。⚠️ `verify_frame_capture` **测不出窗口漂移**(切片/逐区 grab 同用窗口基准会一起漂),它验的是 mss 整帧切片==逐区 grab。`config.py FRAME_CAPTURE 默认 0→1`。
- **砍 sleep(`CAPTURE_INTERVAL_MS` 250→30)**:整小时软泡实测 sleep=0 跑 ~5.6Hz / 切手正常 / CPU 扛得住 / 无质量回归 → 默认降 **30**(留小值不满核空转)。**典型 tick 1.8→5.6Hz(~3×)**。⚠️遗留:部分防抖按 **tick 数**算(按钮去抖/每4tick强刷),低 sleep 下真实时间缩 ~2.6×;切手已实测扛住,**锁更低(→0)前需审 tick-based 计时改墙钟**。
- **1 小时软泡质量**:72 手稳定、**切手金丝雀(button 36→36、active_set)纹丝不动**(→ 排除窗口漂移)、无崩;唯一黄灯=公共牌 jitter(系统性"第 3 张"错位=**ROI 布局问题非 CNN/非漂移**,预存,记 **#245**)。
- **瓶颈交棒**:截屏没了 → 现在大头是 **recognition(`fold_ocr` ~108ms)= 杠杆 B / all-in #243**(stack→0 + 摊牌闸替 fold OCR,顺手干掉这块)。**优化链已自然衔接**:DB(§0 治)→ 截屏(§0′ 治)→ recognition(#243 治)。
- **GPU/显存(为将来把 OCR 搬 GPU 预演)**:**裁图已免费**(numpy 切片 ~µs,**别 GPU 它**——传输开销 > 切片);牌 CNN 已自动用 CUDA(`cnn_classifier.py` `cuda if available`);**VRAM 压测占 10G 跑 37min:0 OOM/error、牌 CNN 正常(13 摊牌 accept)、质量不降** → **显存余量充足,将来 OCR(~1.5G)上 GPU 安全**。硬件优先级 **GPU > CPU 单核 > 核数**;**不换语言**(瓶颈在 GPU 利用 + 架构,非 Python;cv2/torch 重库松 GIL、生态全在 Python)。CNN 漂移=输入分布漂(分辨率/桌皮/亮度)致固定 CNN 静默崩,缺口=无生产漂移哨兵(置信度/物理违规率走势预警)。
- **临时件**:`main.py` 有 `POKEMIR_VRAM_FILL_GB` 显存压测块(默认 0 惰性,用户保留待复用)+ `tools/verify_frame_capture.py`、`tools/probe_allin_yellow.py`/`probe_stack_zero.py`(探针留用)。

---

### §0 实测收口(2026-06-06)⭐ 真瓶颈是 DB 网络写,不是 OCR/截屏

**经过**:杠杆A(配方读 stack,live 10× 已验)+ 杠杆D(整窗抓一次替 37 次 grab)落地后,tick 仍随活跃座摆、有个 **329-929ms 的"谜之 gap"**(任何子计时器都没抓到)。**我两次猜错**(以为 gap=37次grab→D 没用、又以为=GPU异步藏的OCR);**用户从早就质疑 Win↔VPS 网络延迟,被我用 `db_pct≈0` 这个【量错了东西的指标】否决**(db_pct 只计 commit,不计每动作的 `event_repo.create` INSERT 往返)。给 seat 循环加细计时(`seat_parse_persist`)后,**真相现形**:

| 指标 | 写 VPS(Win→VPS 197ms Tailscale) | 写 Win 本地 PG18 |
|---|---|---|
| **persist**(每动作 INSERT) | 41–354ms | **0–1.5ms** |
| **seat_loop**(谜之 gap) | 329–929ms | **8–26ms** |
| **hand_start**(startup 卡,用户肉眼观察) | 520ms+(曾飙 22s) | 首手 CNN 204、后续 0 |
| tick median / Hz | 700–2300ms / 0.2–0.5Hz | **254–799ms / 0.95–1.9Hz** |

**定论**:**tick 的真瓶颈一直是【每个动作一次同步 DB 写、走 197ms Tailscale 往返】**——翻前动作密 → 写得多 → 用户观察的"牌局开始卡一下"。**OCR/截屏都是次要**(杠杆 A 真有用但小、杠杆 D 几乎没用因 grab 本就 12ms)。修法=**DB 迁 Win 本地**(原生 PG18;pipeline 写 localhost 微秒级、Claude 走 Tailscale `100.77.23.17` 偶读)。详细搭建踩坑(原生 PG18 占 5432 冲突 docker / psycopg2 中文 locale GBK 报错 / #210 id 无 server_default 致 diag 裸 INSERT 失败)= 一晚的 ops 史,见 session 转录。

**下一刀(现在干净)**:tick 剩下大头只有 EasyOCR 批读 `fold_ocr ~250` + `action_ocr ~200` → 走 **card_marker 参照差分(判弃牌/活跃)+ 动作词砍/模板化**(杠杆 B),端掉这 ~450ms,冲 4-8Hz。persist/seat_loop/capture/community 全已个位数。

**教训(刻进 [[feedback-data-driven-mandate]])**:断言"X 无影响"前必测【那条具体路径】,别拿 proxy 指标(db_pct≠INSERT延迟)；用户基于物理直觉(高延迟)的 push back 是对的。

---

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
  - **为什么 EasyOCR 啃不动孤立数字(官方源,①)**:EasyOCR 两段 = `Reader.detect()`(CRAFT/DBNet18)+ `Reader.recognize()`(CRNN);`detect()` 官方参数含 `min_size=20` / `text_threshold=0.7` / `low_text=0.4` —— **孤立小数字在检测段就可能因不达阈值被丢框 → 识别段根本不运行 → 返回空**(官方 `/jaidedai/easyocr`)。和我们孤立"0"→`''`、多位数→读对 **(实测)** 对得上。**配方无检测段、不受这些阈值摆布 → 能命中**。(另:理论上调 `detect()` 阈值也能救孤立数字,但配方更干净,不走那条。)
- **"复用老管线+新桩基"= 定点换发动机,不是重建**(前提:封存死因确是性能 → 已确认)。
- **双 OCR(注意力)为什么死**:精心调度错的工具,且和 §15"密集全读"对撞。教训:**发现自己在为昂贵操作设计配给系统时,先问"它非得这么贵吗"**。

### §3 代码侦察发现(标清:事实 / 读码推断)

**热路径架构(事实):**
- `main.py → pipeline/orchestrator.py`,**mss 截屏**(`capture/screen.py`),`_tick` 循环;`CAPTURE_INTERVAL_MS=250`(设计 4Hz),**周期 = tick 耗时 + 250ms sleep(额外加)**;卡 1Hz ⇒ tick 本身 ~750ms。
- **已自带体检仪**:每 tick per-phase 计时 + 打日志(`tick_ms`/`pct_of_tick`,`orchestrator.py:290-493`)——跑起来自报谁慢。注释留有"seat_actions ~410ms 未计时 gap"。
- **名字已 throttle**:`capture_ids` 每 8 tick 才读(`orchestrator.py:391`)——"名字移出热路径"老管线早做了。

**⚠️ 1Hz 真凶不是单一(复盘修正):两块同级嫌疑,且其一非 OCR。**

*嫌疑甲 — 每座 OCR(读码推断,待实测):*
- 每座 OCR:`seat_stack/amount/action_ocr` × 8 座,每 tick 二十几次神经网络调用(detect+recognize 两段)。
- 数字集成点干净:`orchestrator.py:2022-2092 / 1652-1686 / 1290-1299`(均 `self.ocr.read_text(stack_area/amount_area)`)。
- **动作词 OCR 不能顺手砍**:缠在 `detector.py:239 check_action_change` 触发器、且有平行 `events/normalizer.py:infer_action_from_delta`。数字能干净换,动作词缠着。

*嫌疑乙 — 每 ROI 独立截屏(代码 + 官方,复盘新发现,非 OCR):*
- `orchestrator.py` 有 **37 个 `capture_roi` 调用点**,`screen.py:143` 每个都是一次**独立 `self._get_sct().grab(region)`** **(代码)**;
- 官方文档 `/websites/python-mss_readthedocs_io` 证实:**每 `grab(region)` = 一次独立捕获、mss 本就帧率受限**(官方带 fps benchmark 示例)**(官方)**;每 tick 几十次独立 grab → **很可能就是那"~410ms 未计时 gap"的真身,而且它非 OCR**。
- **含义:只换 OCR 未必破 1Hz**——截屏方式是平行瓶颈(治法见 §4 杠杆 D)。
- (具体 ms / DXcam 对比是博客来的、**官方无 → 待本地实测**,见 §5/§6。)

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
| **A 换配方** | 每次读**更便宜**(治 OCR) | ✅ **已落地默认开**(`DIGIT_RECIPE_LIVE`,stack 走 DigitReader) |
| **B 内容闸门(参照差分)/ 砍 recognition** | 空区**根本不读** + 脏区**挡门外**;现具体化为 all-in stack→0 + 摊牌闸**替 fold OCR** | **下一刀 = #243**(fold_ocr 108ms,截屏治完后的新大头) |
| **C 变化闸门** | 上 tick 没变**不深处理** | 全新,概念最简单(已有 diff 缓存雏形) |
| **D 截屏整帧化(③)** | **几十次独立 grab → 1 次整帧 grab + numpy 切片** | ✅ **2026-06-09 已落地默认开**(`FRAME_CAPTURE`;字节级验过 §0′;154→12ms) |

> A = "读得便宜",B/C = "少读/不读",**D = "少抓"**。前三治 OCR/识别,**D 治每 tick 几十次独立截屏**(嫌疑乙)。**四根都得算,只动 A 未必破 1Hz。**
> **D 备选**:Windows 上 **DXcam** 比 mss 更快(**博客/待验**,官方无;我们运行时正是 Windows)→ 若整帧化后截屏仍是瓶颈再上。可先用官方 `MSS.performance_status` 量截屏成本。

**杠杆 B 的两条用途(用户直觉的具现):**
- **B1 空 ROI → 跳过 OCR(速度)**:amount 区绝大多数 tick 对绝大多数座是空的却每 tick 被读 → 微秒级"空就跳"。砍掉的读可能比换配方还多。描述子用**墨占比/与参照均差**(比 phash 更便宜更准)。
- **B2 遮挡/横幅 → 拒读(精度)**:区域既不像"空"也不像"该有内容" = 外来物盖着(滚动公告横幅,一条亮带同扫一排座)→ 拒这批读。**廉价杀"真幻影"FP**(可能不必等求解器 #226)。phash 适合这类图案判定。
- **车道**:它是**在不在/什么状态**的探测器,**不是内容读取器**——粗哈希**未必可靠区分相近多位数**(如 128/228),靠它读数不稳。它管闸门/路由,识别管读数。
- **坑**:亮度(弃牌变暗)/动画会骗它 → 需亮度归一 + 时序稳定(状态持续几帧才认);参照按桌皮/座采集。

### §5 落地路线(分阶段)

- **Stage 0 — 全 tick 拆解基准(④,最先、最便宜)**。不能只测 OCR,否则换完 OCR 才发现卡在截屏。要量:
  - **(a) 全 tick 成本分解**:截屏 / OCR / DB / 那"~410ms gap" 各占多少(用现成 per-phase 计时 + 把 `capture_roi` 单独计时;截屏可用官方 `MSS.performance_status`);
  - **(b) 配方 vs EasyOCR 的 ms**——**且公平对比**:配方(CPU)vs EasyOCR(**GPU + batch,即生产配置**,非 CPU 裸跑);配方成本**随 exemplar 池大小涨**(每格 × 全部样本做相关),必须实测不能假设"轻几个量级";
  - **(c) `seat_action_ocr` 占 tick 比**(决定要不要单开动作词那仗);
  - **(d) amount/stack 每 tick 读空频率**(量杠杆 B 天花板);
  - **(e) 几十次独立 grab vs 整帧抓一次 的 ms 差**(量杠杆 D 天花板)。
  - 这几个数一起,把"破 1Hz 到底靠哪根杠杆、各值多少"定死。
- **Stage 1 — 杠杆 A:换配方**。`POKEMIR_DIGIT_RECIPE` flag 守卫(默认关、一键回滚),init 按 profile 加载 `DigitReader`;数字区三处接"**配方为主 + EasyOCR 仅 None 兜底**";动作词不动。
- **Stage 2 — 杠杆 B:内容闸门(用户想法,本路线一等阶段)**。接回孤儿 baseline JSON(或改用墨占比参照);活跃座**逐区读前**加微秒级内容闸门(**第一刀 = `amount_area`**);flag 守卫(如 `POKEMIR_REGION_GATE`)可单开、可叠 A;B2 横幅检测为同层精度副产物。
- **Stage 3 — live 实测(真 test)**。A(+B)开,真桌跑几分钟,读自报 `tick_ms` → 破不破 1Hz;眼校配方在**真实渲染**下准度(n=1 录像≠live)。
- **杠杆 D 截屏整帧化(独立、低风险,按 Stage 0 (a)/(e) 结果排序)**。把热路径几十次 `capture_roi` 改成"整帧 grab 一次 + numpy 切片"(replay 已是此模式),纯重构、不依赖 OCR 决策;若 Stage 0 显示截屏占大头,**它可能比换配方更先做**。
- **Stage 4 —(若验成)再榨 + 清债**。杠杆 C 变化闸门;砍额外 250ms sleep;(Windows 截屏仍瓶颈则评估 DXcam);删死的 `ATTENTION_MODE`。

### §6 待拍板 & 诚实 caveat

**待拍板:** ① 数字读法:**配方为主+兜底**(荐)vs 纯替换;② 动作词 OCR:**本轮不碰、Stage 0 先量占比**(占比大才单开下一仗:封闭词表模板化 / 靠 stack 跌幅推动作)。

**caveat(标清可信度):**
- 全是**读码推断 + 离线 n=1**,live 数未进;模板 / baseline **按桌皮**;配方 **live 渲染准度未验**。
- **"配方比 EasyOCR 轻几个量级"= 未实测假设**,配方成本随 exemplar 池大小涨 → Stage 0 (b) 实测,不当结论。
- **"~410ms gap = 截屏" 是强假设但未实测**;**mss 具体 ms / DXcam 快 3-4 倍 = 博客/待验**(官方文档无)→ 用官方 `performance_status` 本地量。
- **粗哈希分不出相近多位数(128/228)= 一般性论断**,非逐例实测;闸门论点不依赖该具体例。
- **高帧反噬**:tick 越快抓到动画中间帧越多 → live 同样靠 plateau/时序融合滤(非越快越好)。
- **DB 每 tick 写库成本**(`db_total_ms` 已计时)= 另一块非 OCR 成本,Stage 0 (a) 顺带量。
- **可用性**:截屏需 WePoker 窗口可见/前台 → 跑着时那块屏你用不了(live 实操约束,未解)。

**校验小结(本次复盘)**:EasyOCR 两段架构 + `detect()` 阈值丢框机制 = **官方证实**;mss "每 grab 一调用/帧率受限" = **官方证实**;mss 具体数字 / DXcam = **博客,降级待本地验**。孤立"0"读空 = **本仓库 A/B 实测**。

---

## 关联记忆 / 待办

- **待办**:Stage 0 **全 tick 拆解**(截屏/OCR/DB/gap 分解 + 配方vsOCR ms + action占比 + 读空频率 + 整帧vs逐ROI grab)→ 定盘哪根杠杆各值多少 → 走出温室。
- **现成件**:`pipeline/digit_reader.py`(杠杆 A)、`tools/build_digit_templates.py`、`tools/probe_zero.py`、孤儿 `tools/capture_empty_seat_baseline.py`(杠杆 B 待接)。
- **关联记忆**:[[digit-ocr-stack-recipe]](配方)、[[constraint-solver-paradigm]](§15 stack-centric / 动作靠跌幅推)、[[data-reliability-50-70-percent]](95% 捕获主线)、[[hand-segmentation-corroboration]](离线夹具 recall 100% 金标准)。
