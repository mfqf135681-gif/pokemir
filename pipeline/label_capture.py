"""信源验证标注【采集侧】(2026-06-10,见 requirement-discussions/主题-信源验证.md)。

主程序全速 tick 内【旁路抽头】目标信号:把【识别器实际吃的那块 crop + 逐帧读值 + 宽图(整窗帧画 ROI 框)】
存盘,**不阻塞 tick、不改任何 production 行为**。供 tools/label_signal.py 盲标产 ground truth。

五硬约束落点:
  ① 全速 tick 旁路抽头 —— 这是个被动 tap,主循环照常全速跑;抽的就是 production 那条路实际产出的值。
  ③ 事件触发采样 —— _EVENT_REASONS(变值/全下/摊牌/换手)必采(覆盖尾部:大池/全下),稳态按时间地板稀采。
  ④ 宽图防框歪 —— 除 crop 另存整窗帧并画出 ROI 框,标注时能看出框有没有切歪/框错位。
  附:crop 必须是识别器**实际吃的那块**(调用方传读时的 img,非事后重抓)。

空 LABEL_SIGNAL → enabled=False → 所有 tap() 第一行 return,零开销零行为变化。
"""
import json
import os
import time

try:
    import cv2
except ImportError:  # Linux smoke 无 cv2 时不致命(采集侧只在 Win live 跑)
    cv2 = None

from config import LABEL_SIGNAL, LABEL_DIR


class LabelCapturer:
    # 约束③:这些 reason 必采(尾部覆盖);其余(稳态 "tick")按 floor_sec 时间地板稀采。
    _EVENT_REASONS = {"change", "allin", "showdown", "hand_start", "big"}

    def __init__(self, signal: str = None, out_dir: str = LABEL_DIR, floor_sec: float = 8.0):
        self.signal = (signal if signal is not None else LABEL_SIGNAL) or ""
        self.enabled = bool(self.signal) and cv2 is not None
        self.floor_sec = floor_sec
        self._last_emit = 0.0
        self._n = 0
        self._session_dir = None
        self._jsonl = None
        if self.enabled:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self._session_dir = os.path.join(out_dir, f"{self.signal}_{stamp}")
            os.makedirs(self._session_dir, exist_ok=True)
            self._jsonl = os.path.join(self._session_dir, "samples.jsonl")

    def tap(self, signal, crop, read_value, raw_text=None, wide_frame=None,
            roi=None, hand_id=None, reason="tick"):
        """读完目标信号即调一次。signal≠目标 / 未开 → no-op。
        crop = 识别器实际吃的那块(读时的 img);wide_frame = capturer.get_cached_frame()(整窗,可None)。"""
        if not self.enabled or signal != self.signal:
            return
        if crop is None or getattr(crop, "size", 0) == 0:
            return
        now = time.time()
        is_event = reason in self._EVENT_REASONS
        if not is_event and (now - self._last_emit) < self.floor_sec:
            return  # 稳态稀采:时间地板内不重复存(约束③不让"按秒"刷屏淹没尾部)
        self._last_emit = now
        sid = self._n
        self._n += 1

        crop_name = f"{sid:05d}_crop.png"
        cv2.imwrite(os.path.join(self._session_dir, crop_name), crop)

        wide_name = None
        if wide_frame is not None and getattr(wide_frame, "size", 0) > 0:
            wide = wide_frame.copy()
            if roi is not None:
                try:
                    cv2.rectangle(wide, (roi.left, roi.top),
                                  (roi.left + roi.width, roi.top + roi.height),
                                  (0, 0, 255), 2)  # 红框=识别器切的位置 → 眼验框歪
                except Exception:
                    pass
            wide_name = f"{sid:05d}_wide.png"
            cv2.imwrite(os.path.join(self._session_dir, wide_name), wide)

        rec = {
            "id": sid, "ts": round(now, 3), "signal": signal,
            "read_value": read_value, "raw_text": raw_text,
            "reason": reason, "hand_id": str(hand_id) if hand_id else None,
            "crop": crop_name, "wide": wide_name,
        }
        with open(self._jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
