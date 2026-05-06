"""Screen capture test — verifies we can capture a window/region and save a screenshot."""

import sys
from pathlib import Path

# Add project root to sys.path so we can import capture module
sys.path.insert(0, str(Path(__file__).parent.parent))

from capture.screen import ScreenCapturer, ROIRegion


def main():
    print("=" * 50)
    print("Screen Capture Test")
    print("=" * 50)

    capturer = ScreenCapturer()

    # List available monitors
    monitors = capturer.list_monitors()
    print(f"\nAvailable monitors: {len(monitors)}")
    for i, m in enumerate(monitors):
        print(f"  [{i}] {m['width']}x{m['height']} at ({m['left']}, {m['top']})")

    if len(monitors) < 1:
        print("ERROR: No monitors found.")
        return 1

    # Use primary monitor (index 1; 0 is the "all monitors" virtual screen)
    capturer.select_monitor(1)
    print(f"\nSelected: monitor 1 ({monitors[1]['width']}x{monitors[1]['height']})")

    # ── Full screenshot ──────────────────────────────────
    print("\nCapturing full screen...")
    full = capturer.capture()
    print(f"  Full screen: {full.shape} (H,W,C) — {full.dtype}")

    # Save with PIL
    from PIL import Image
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    full_path = output_dir / "capture_full.png"
    # mss returns BGRA; PIL expects RGBA
    img = Image.frombytes("RGB", (full.shape[1], full.shape[0]), full[..., [2, 1, 0]].tobytes())
    img.save(full_path)
    print(f"  Saved: {full_path}")

    # ── Center crop test ─────────────────────────────────
    w, h = monitors[1]["width"], monitors[1]["height"]
    crop_w, crop_h = 400, 300
    center_roi = ROIRegion(
        name="center_test",
        left=(w - crop_w) // 2,
        top=(h - crop_h) // 2,
        width=crop_w,
        height=crop_h,
    )

    print(f"\nCapturing center crop ({crop_w}x{crop_h})...")
    crop = capturer.capture_roi(center_roi)
    print(f"  Crop: {crop.shape} (H,W,C) — {crop.dtype}")

    crop_img = Image.frombytes("RGB", (crop.shape[1], crop.shape[0]), crop[..., [2, 1, 0]].tobytes())
    crop_path = output_dir / "capture_crop.png"
    crop_img.save(crop_path)
    print(f"  Saved: {crop_path}")

    print("\n" + "=" * 50)
    print("Capture test complete! Check tests/output/ for screenshots.")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
