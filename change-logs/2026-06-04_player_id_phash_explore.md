# 玩家 ID 识别:phash 探索 + 方法定型

## 概述
C 档(玩家身份)。痛点:OCR 名漂移碎片化画像(find_player_aliases 事后补救)。
探索 phash(名字像素)当稳定 key → **同座成、跨侧崩**。方法定型见记忆 [[player-id-recognition-approach]]。

## phash-ID 判决
- **同座/同侧 ✓**:中位Δ≈0-2、贴众数 80-95%(3录像);不同玩家 30-45 → 分得开。
- **跨侧 ✗(实锤)**:自定义房可控录(用户+朋友各左座→右座),`--frames` 直接比干净帧:**同人跨侧 41-50 ≈ 不同人 50-62,重叠**。根因:名字左/右座 UI 渲染镜像(框已验对称,非框锅)。+ 变长名定长框本就脆。→ **phash 不当独立 key**。

## ID 方法(定型)
OCR 名为主 key + **多帧众数**(源头灭漂移)+ **跨座/session 靠 OCR**(位置无关)+ **同座相似名候选用 phash 确认**(同像素=同指纹,防误并不同人)+ find_player_aliases 模糊合并人审。各管强项。

## 工具 / 副产(tools/id_phash.py)
`--cluster-seats`(聚类,但会撞共同态如空座)/`--cross L:R`(时序切半,假设换座在中点→不稳)/**`--frames 文件:座`(直接比指定帧,零猜测=干净答案)**。
- card_marker = 镜像准锚(验:尺寸一致+镜像差0);id ROI 已按 card_marker 锚算齐(宽122,中心镜像)。
- `record_frames.py`:副屏/负坐标 `--backend mss`;多窗口(Edge+Chrome都开WePoker)交互选/`--window-idx`。
- 测法教训:50%切半不稳;直接比指定干净帧才settle(--frames)。

## 未做
OCR-name 多帧众数 + phash 同座确认接入 find_player_aliases(C档低频,优先级低于主线 §15)。
