"""信源验证标注【采集侧】(2026-06-10,见 requirement-discussions/主题-信源验证.md)。

主程序全速 tick 内【旁路抽头】目标信号:把【识别器实际吃的那块 crop + 逐帧读值 + 宽图(整窗帧画 ROI 框)】
存盘,**不阻塞 tick、不改任何 production 行为**。供 tools/label_signal.py 盲标产 ground truth。

五硬约束落点:
  ① 全速 tick 旁路抽头 —— 被动 tap,主循环照常全速跑;抽的就是 production 那条路实际产出的值。
  ③ 事件触发采样 —— 去重对【上一张采的读值】(非 production 状态,治"production 卡住→狂存同值"):
     读值变了立即采(覆盖所有不同值含尾部大池),稳态每隔 interval 补一张(清晰样本喂模板);采够 max 停。
  ④ 宽图防框歪 —— 除 crop 另存整窗帧并画 ROI 框。
  附:crop 必须是识别器**实际吃的那块**(调用方传读时的 img)。

空 LABEL_SIGNAL → enabled=False → tap() 全 no-op,零开销零行为变化。
"""
import json
import os
import time

try:
    import cv2
except ImportError:  # Linux smoke 无 cv2 时不致命(采集侧只在 Win live 跑)
    cv2 = None

from config import LABEL_SIGNAL, LABEL_DIR, LABEL_MAX, LABEL_INTERVAL_SEC

_UNSET = object()


class LabelCapturer:
    def __init__(self, signal: str = None, out_dir: str = LABEL_DIR,
                 interval_sec: float = LABEL_INTERVAL_SEC, max_n: int = LABEL_MAX):
        self.signal = (signal if signal is not None else LABEL_SIGNAL) or ""
        self.enabled = bool(self.signal) and cv2 is not None
        self.interval = interval_sec
        self.max_n = max_n
        self._last_value = {}         # {seat: 上一张采过的读值}(去重基准,非 production 状态;表级 seat=None)
        self._last_emit = {}          # {seat: 上次采样时刻}
        self._n = 0
        self._done_announced = False
        self._session_dir = None
        self._jsonl = None
        if self.enabled:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self._session_dir = os.path.join(out_dir, f"{self.signal}_{stamp}")
            os.makedirs(self._session_dir, exist_ok=True)
            self._jsonl = os.path.join(self._session_dir, "samples.jsonl")
            print(f"[label] 采集 '{self.signal}' → {self._session_dir}(目标 {max_n} 张,间隔 {interval_sec}s)",
                  flush=True)

    def _should_emit(self, read_value, now, seat):
        """去重 + 间隔决策(按座独立)。读值变了(对该座上一张采的)→采;否则每 interval 补一张。"""
        changed = (read_value != self._last_value.get(seat, _UNSET))
        if changed:
            return "change"
        if (now - self._last_emit.get(seat, 0.0)) >= self.interval:
            return "interval"
        return None

    def tap(self, signal, crop, read_value, raw_text=None, wide_frame=None,
            roi=None, hand_id=None, seat=None):
        """读完目标信号即调一次。signal≠目标 / 未开 / 采满 → no-op。seat=每座信号(amount/+xx)的座号,表级省略。
        crop = 识别器实际吃的那块;wide_frame = capturer.get_cached_frame()(整窗,可 None)。"""
        if not self.enabled or signal != self.signal:
            return
        if self._n >= self.max_n:
            if not self._done_announced:
                print(f"[label] ✅ 已采满 {self.max_n} 张 → 可 Ctrl-C 停,然后 "
                      f"python tools/label_signal.py {self._session_dir}", flush=True)
                self._done_announced = True
            return
        if crop is None or getattr(crop, "size", 0) == 0:
            return
        now = time.time()
        reason = self._should_emit(read_value, now, seat)
        if reason is None:
            return
        self._last_value[seat] = read_value
        self._last_emit[seat] = now
        sid = self._n
        self._n += 1

        _tag = f"_s{seat}" if seat is not None else ""
        crop_name = f"{sid:05d}{_tag}_crop.png"
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
            wide_name = f"{sid:05d}{_tag}_wide.png"
            cv2.imwrite(os.path.join(self._session_dir, wide_name), wide)

        rec = {
            "id": sid, "ts": round(now, 3), "signal": signal, "seat": seat,
            "read_value": read_value, "raw_text": raw_text,
            "reason": reason, "hand_id": str(hand_id) if hand_id else None,
            "crop": crop_name, "wide": wide_name,
        }
        with open(self._jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
