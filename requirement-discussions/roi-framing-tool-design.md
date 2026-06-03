# ROI 框选工具改造设计(方案 A — 两阶段放大 + 裁剪预览)

> 状态:**设计待实现**(2026-06-03 拍板 A;用户离 Win,落文档,回 Win 再写码)。
> 目标:降低小目标(牌角标记/弃牌区/seat-id 等)框选风险,**不碰 cv2 交互盲区**。
> 实现位置:`tools/roi_config.py`(主力框选 `select_roi`,101 行,用 `cv2.selectROI`)。

## 1. 痛点 + 为什么不能直接满足"透明/窄边框"
用户痛点:整张 1920×1080 截图被缩放进 1280×720 窗 → 小 ROI 在屏上极小、框不准;且 `selectROI` 的选框遮住要看的几像素。

**context7 OpenCV 4.x 官方签名坐实**:
```
selectROI(windowName, img, showCrosshair=true, fromCenter=false, printNotice=true) → Rect
```
参数只有这 5 个 —— **无 color、无 thickness**。选框外观焊死在 highgui C++ 里,Python 层无入口。
→ **"边框透明/变窄"在 selectROI 上做不到**;要它们必须弃 selectROI 改自绘 `cv2.rectangle`(thickness/lineType 可控 + addWeighted 透明)= **方案 B**。B 重写鼠标交互,落在 cv2 GUI 盲区([[dev-rule-validate-blind-spots]],2026-05-27 cv2 翻车案底),Win 全程试飞、翻车概率高 → **暂不做,仅当 A 不够再回头**。

## 2. 方案 A:三件套(全程继续用官方 selectROI,放大让"透明/窄边"需求消失)

### A1. `fromCenter=True`(零成本,先上)
`cv2.selectROI(win, img, showCrosshair=True, fromCenter=True)` —— 从**中心往外拖**,框对称小目标更顺手。一行改动。

### A2. 两阶段放大框选(核心)
1. **粗框**:在 fit-to-screen 的整图上 `selectROI` 大致圈出目标邻域 `(cx,cy,cw,ch)`。
2. **裁剪 + 放大**:在粗框四周留 padding(如各 +20px)裁出 `crop = img[cy-pad : cy+ch+pad, cx-pad : cx+cw+pad]`,放大 `SCALE`×(建议 6–8,`cv2.resize`,`INTER_NEAREST` 保边缘锐利便于对齐)。
3. **精框**:在放大图上再 `selectROI` 得 `(fx,fy,fw,fh)`。**8× 视图里 2px 选框几乎不挡内容 → 透明/窄边需求自然消失。**
4. **坐标回映**(关键,务必对):
   ```
   crop_x0 = cx - pad ; crop_y0 = cy - pad
   x = crop_x0 + round(fx / SCALE)
   y = crop_y0 + round(fy / SCALE)
   w = round(fw / SCALE)
   h = round(fh / SCALE)
   ```
   注意 padding 裁剪要 clamp 到图像边界(`max(0,...)`),回映时用 clamp 后的实际 crop_x0/y0。

### A3. 裁剪预览确认(降风险关键)
精框完,把最终 `img[y:y+h, x:x+w]` 放大显示一遍:
- `SPACE` = 接受并保存
- `R` = 重来(回 A2 粗框)
- `ESC` = 跳过本 ROI(保留旧值,与现有 ESC 语义一致)
让用户**所见即所存**,当场发现框偏。

## 3. 复用现成省事路(并行可用)
- 8 座同尺寸 ROI(牌角标记):`place_roi_by_click` + `--copy-size`(113 行)已支持"框一次定尺寸,每座点中心即放置"。叠 A3 预览即够用,**未必都要两阶段**。
- `--verify`(207/317 行)整 profile 预览仍保留。

## 4. 未验证、用前必查(不凭印象)
- **`cv2.selectROIs`(复数,一次框多个)**:据查 OpenCV 有,但 context7 这轮**没返回其签名** → 真要用先验"本机 cv2 版本在不在、签名啥样",别假设。
- **A 整体交互**:cv2 GUI 是盲区,Linux 无 display 验不了 → 写完 **Win 端用户试飞**,change-log 分"Linux 静态 + Win 实测"两段([[env-tooling-constraints]] §5)。

## 5. 验收
- 框一个小目标(如牌角)用时/重框次数显著降;预览能当场抓出框偏。
- 回映坐标与直接在原图框选一致(误差 ≤1px,因 round)。
- 不破坏现有 `--copy-size` / `--verify` / ESC-跳过 / Q-退出 语义。
