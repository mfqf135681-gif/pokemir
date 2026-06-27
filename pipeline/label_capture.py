"""信源验证标注【采集侧】(2026-06-10,见 requirement-discussions/主题-信源验证.md)。

主程序全速 tick 内【旁路抽头】目标信号:把【识别器实际吃的那块 crop + 逐帧读值 + 宽图(整窗帧画 ROI 框)】
存盘,**不阻塞 tick、不改任何 production 行为**。供 tools/label_signal.py 盲标产 ground truth。

支持【多信号一局同采】:LABEL_SIGNAL="potprev,amount" → 各信号一个独立 session 目录(分开盲标)。
每座信号(amount/+xx)带 seat;表级信号(pot_size/potprev)seat=None。

五硬约束落点:① 全速 tick 旁路抽头(被动 tap)③ 去重对【该信号该座上一张采的值】+ 稳态 interval 补
④ 宽图画 ROI 框防框歪。附:crop 必须是识别器实际吃的那块。空 LABEL_SIGNAL → enabled=False → 全 no-op。
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
        raw = signal if signal is not None else LABEL_SIGNAL
        self.signals = {s.strip() for s in (raw or "").split(",") if s.strip()}
        self.enabled = bool(self.signals) and cv2 is not None
        self.interval = interval_sec
        # 去重窗口(2026-06-27):同(座,值)在此秒内只采一次 → 治"出动作→回idle→再出"振荡 +
        # 稳定值每 interval 反复采 两类重复帧。不同值照采(保多样性);过窗后同值可再采。
        self.dedup_window = float(os.getenv("POKEMIR_LABEL_DEDUP", "8.0"))
        self.max_n = max_n
        self._st = {}   # sig -> {dir, jsonl, n, seen{(seat,vrepr):t}, done}
        if self.enabled:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for sig in self.signals:
                d = os.path.join(out_dir, f"{sig}_{stamp}")
                os.makedirs(d, exist_ok=True)
                self._st[sig] = {"dir": d, "jsonl": os.path.join(d, "samples.jsonl"),
                                 "n": 0, "seen": {}, "done": False}
                print(f"[label] 采集 '{sig}' → {d}(目标 {max_n} 张,同值去重窗 {self.dedup_window}s)", flush=True)

    def tap(self, signal, crop, read_value, raw_text=None, wide_frame=None,
            roi=None, hand_id=None, seat=None):
        """读完目标信号即调一次。signal 不在采集集 / 未开 / 该信号采满 → no-op。
        seat=每座信号(amount/+xx)的座号,表级省略。crop=识别器实际吃的那块;wide_frame=get_cached_frame()。"""
        if not self.enabled or signal not in self.signals:
            return
        st = self._st[signal]
        if st["n"] >= self.max_n:
            if not st["done"]:
                print(f"[label] ✅ '{signal}' 已采满 {self.max_n} 张 → 可 Ctrl-C 停,然后 "
                      f"python tools/label_signal.py {st['dir']}", flush=True)
                st["done"] = True
            return
        if crop is None or getattr(crop, "size", 0) == 0:
            return
        now = time.time()
        # 去重:同(座,值)在 dedup_window 内只采一次(治振荡 + 稳定值反复采)。None/idle 同理。
        key = (seat, str(read_value))
        if now - st["seen"].get(key, 0.0) < self.dedup_window:
            return
        reason = "new"
        st["seen"][key] = now
        sid = st["n"]
        st["n"] += 1

        _tag = f"_s{seat}" if seat is not None else ""
        crop_name = f"{sid:05d}{_tag}_crop.png"
        cv2.imwrite(os.path.join(st["dir"], crop_name), crop)

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
            cv2.imwrite(os.path.join(st["dir"], wide_name), wide)

        rec = {
            "id": sid, "ts": round(now, 3), "signal": signal, "seat": seat,
            "read_value": read_value, "raw_text": raw_text,
            "reason": reason, "hand_id": str(hand_id) if hand_id else None,
            "crop": crop_name, "wide": wide_name,
        }
        with open(st["jsonl"], "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
