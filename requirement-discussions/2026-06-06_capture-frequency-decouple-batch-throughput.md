# 拾取频率 / 识别吞吐架构:recognize-only + 解耦 + 实时(2026-06-06~07 讨论+实测)

**状态:实测已出 —— 🔴 批处理证伪,recognize-only(跳检测)才是真杠杆**。
关联:[[constraint-solver-paradigm]](95% 攻法)· [[data-reliability-50-70-percent]](高帧+时序融合)·
#235(铲 fold_ocr → 活跃集 phash,瓶颈头号)· #237(原"升批处理默认",**已改写为"接 recognize-only 路径"**)· #226 二期(序列重建,吊在频率上)。

---

## 0. 为什么重要
拾取频率是整条范式命门:**序列重建(#226 二期)、rebuy 时间闸、时序投票(数字准度)全吊在它上**。
"提频率"含两个完全不同的频率,先劈开,否则数字是假的。

## 1. 现状(grep 实底,非印象)
- **抓屏 = mss**;杠杆 D.1 已把"~37 次逐区 grab"压成"整窗抓一次切片";live `capture_frame` ≈ **0ms** → 捕获不是瓶颈。
- **`CAPTURE_INTERVAL_MS=250ms`**(sleep);`effective_hz=1000/(tick_avg+250)`。~1Hz ⟹ tick_avg 处理主导,非 sleep。
- **per-phase 计时内建 + `diag.emit("pipeline.tick_stats")` 落 postgres `diagnostic_events`**(Claude MCP 直读,不用贴 log)。
- **DB IO 实测 0.1ms**(T83 推翻"700ms"凭印象,错 7000 倍)→ DB 不是瓶颈。

## 2. 🔴 实测推翻批处理(本轮核心)
### 2a. live per-phase(2026-06-07,当前态 OCR_BATCH=0)
| phase | avg_ms | %tick | 性质 |
|---|---|---|---|
| **seat_fold_ocr** | **417** | **46.6%** | 全字符集(`allowlist=""`,认 ALL IN/timer/头像)逐座,**单项最大** |
| **seat_action_ocr** | **242** | **27%** | 文本逐座 |
| seat_amount/stack_ocr | 32/25 | <4% | digit-recipe,**便宜**(没拖后腿) |
| seat_timer_ocr | 0 | 0% | 批了趋零 |
| capture/CNN/db | ~0 | ~0 | 全免费 |

**bimodal 真因 = 该 tick 要 OCR 几个座,不是 batch 开没开**:全 skippable(弃/手间)→ **~95ms(~10Hz)**;多座活跃→ fold+action 撑到 **~820-1088ms(~1Hz)**。**忙=慢=恰是多人抢动作最需速度时**。per-seat OCR 随活跃座**线性涨**。

### 2b. 控制变量 bench(`run_bench_tick.cmd`,固定帧 82 ROI,无手局干扰)
```
A 逐ROI readtext(现状)  : 1750ms → 0.57Hz
B readtext_batched       : 1984ms → 0.50Hz  ×0.9  ← 批处理比逐ROI还慢!
C recognize-only(bs=32) :  559ms → 1.79Hz  ×3.1  ← 真杠杆
A_num EasyOCR(数字区)   :  316ms;  D 配方 : 34.8ms  ×9.1
```

### 2c. U-turn 结论
- **批处理(B)×0.9 不是杠杆,反而慢 13%**。因 `n_width=max×scale` 把异构 ROI 的小图全上采样到最大尺寸 → 浪费算力 > 省的 launch。**§6 当初的担心,实测坐实。**
- **T73 标的"批处理 3.7x"误标**:那是 T72(仅GPU 5137ms)→ T73(1369ms),提速更可能来自 GPU async 成熟/别的改动,被挂在 batch 名下。**控制 bench 比旧 live before/after 可信。**
- **真杠杆两个**:① **recognize-only(跳过 CRAFT 检测,喂固定 bbox)×3.1 —— live 没接**(live 走 A=readtext 带检测);② **digit 配方 ×9.1**(已部署 stack/amount)。
- **教训**:差点去"恢复 OCR_BATCH"(#237 旧范围),靠 controlled bench 拦下——"性能数字必先 benchmark、别信旧标签"现场。[[data-driven-mandate]]

## 3. 两个频率,差两个数量级
- **① 事件探测(phash 看门狗)**:[context7] DXcam 60-120fps;phash 小 ROI 微秒级 → **现实 30-60Hz**。⚠️ Win 黑屏/受保护窗口未验。**治"漏动作"的解药。**
- **② 识别(OCR/CNN)**:现忙窗 ~1Hz;**recognize-only + #235 同步路径目标 4-10Hz**(bench 自注:recognize-only 按 allowlist 分 2-3 组仍远少于 A)。

## 4. 两条路
- **路 A:让同步处理变便宜** = **#235(fold→phash)+ recognize-only(text/action)+ 配方(已)**。若到 4-10Hz,**也许够喂序列重建,解耦的硬仗可省/可推后**。
- **路 B:解耦捕获与识别**(若 4-10Hz 仍不够):
  - 快线程 mss/dxcam 抓小 ROI+phash 30-60Hz → 变了塞 buffer(裁剪+单调时间戳);慢消费者拉、识别、喂求解器,**可滞后**。
  - 🔑 反框:复盘期识别**不需实时**(重建历史);**捕获必须完整密集,识别只要平均抽干 buffer**。

## 5. 实时识别(后期)—— 同一条管线
- **实时 ≠ 帧率识别**。德州回合制,真 deadline = **轮到 hero 决策时桌态已重建到位**,hero 思考时间(秒级)缓冲。
- → **实时 = 同一条(解耦)管线**,"识别可滞后"收紧成"hero-turn 前抽干 buffer"。deadline 宽,不换架构。
- **"把显卡用好" ≠ 多开模型 ≠ 批处理**(实测 batch 无益)。**正解 = recognize-only(跳检测)+ 配方;endgame = 自训小模型(#225)→ TensorRT(Blackwell sm_120)批量 <5ms**。

## 6. 为什么批处理在异构 ROI 上反而慢(实测归因)
`readtext_batched` 把整批 resize 到 `n_width/n_height=max尺寸×scale`(EasyOCR 统一尺寸)→ **小 ROI(timer)被上采样到大 ROI(fold)的尺寸,per-image 算力暴涨**,省下的 launch 开销补不回。
→ 唯一能批得划算的是"同尺寸+同 allowlist"小组(如 timer×全座),但**总收益 < recognize-only,不值得做**。
→ 若将来解耦后真要批,必须 **per-size 分组**(dynamic micro-batch),不能混尺寸。

## 7. 假设状态(✅已答 / 🔴待测)
1. ✅ **750ms 是什么 bound**:**EasyOCR detection(CRAFT)阶段成本** —— recognize-only 跳过它得 ×3.1。非 CPU-Python、非 DB、非 capture。
2. ✅ **批处理值多少**:**负的**(×0.9)。#237 旧前提错,改写。
3. ✅ **outlier 慢 tick 是谁**:**bimodal = 活跃座数**(per-seat OCR 线性),非 hand 转换/CNN。
4. 🔴 **phash 看门狗动画假阳性**:计时圆环每秒脉动 = 恒定 phash 变化但非动作;须盯指针"座位身份"非像素。
5. 🔴 **GIL/线程**:快捕获 + OCR 抢 GIL(可能共存,最坏退 multiprocessing)。
6. 🔴 **mss 小 ROI 实际 fps** + Win 黑屏/受保护窗口坑。
> 4-6 仅在路 A 到不了 4-10Hz、必须上路 B(解耦)时才需回答。

## 8. 测量进度
- ✅ per-phase(2a)· ✅ 控制 bench A/B/C/D(2b)· ✅ GPU/CPU bound 定性(§7.1)。
- 🔴 **phash 计数丢弃脚本**(mss 小 ROI+phash+计数,不上 OCR)→ 相邻两 tick 间 phash 变几次 + 顺带量 mss 小 ROI fps。**仅在路 A 验完仍不够时才跑**。

## 9. 决定(实测后重排)
- **批处理出局**(实测 ×0.9)。**先走路 A 同步三刀**,再看要不要路 B 解耦:
  1. **#235**:fold_ocr(417ms,46.6%)→ 活跃集 phash(不 OCR)。**单项最大 + 顺带治 silent-fold 漏读**。
  2. **recognize-only**:text/action ROI 从 `readtext`(带检测)换成 recognize-only(喂固定 bbox)。×3.1。**替掉 #237 旧的批处理范围**。
  3. **配方 ×9.1**(数字)已部署。
- **干完路 A 三刀重测 tick Hz**:到 4-10Hz → 序列重建可能够用,**解耦(路 B,§7.4-6 硬骨头)可省/推后**;仍不够 → 再上路 B。
- **顺序铁律**:先测再建。本轮"批处理"就是没测先信旧标签、被 controlled bench 拦下的活案例。
