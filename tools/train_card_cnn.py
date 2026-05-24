"""Train a small two-head CNN to classify poker card images (rank + suit).

Architecture:
    Shared backbone (5 conv blocks ≈ 200K params) → split heads:
    - rank head: 13 classes (2-9, T, J, Q, K, A)
    - suit head: 4 classes (s, h, d, c)

Two-head design lets each classifier see all 31 fixtures (vs 52-way single
classification which would see ≤2-3 samples per class). Augmentation
(brightness ±30%, rotation ±5°, crop jitter, HSV jitter) expands 31 base
fixtures to ~1500 training samples — enough to cover bright/dim variants
(WePoker fold-grey state) and ROI offset noise.

Usage (Win, GPU strongly recommended; 5070 Ti / similar):
    .\\.venv\\Scripts\\python.exe tools\\train_card_cnn.py
    .\\.venv\\Scripts\\python.exe tools\\train_card_cnn.py --epochs 80 --augmentations 50

Output:
    models/card_cnn.pth  (state_dict + metadata)

Red line compliance:
    R-1 / image-only: only reads local fixture PNGs; no network or DOM.
    R-3: model and weights stay local; no upload anywhere.
    R-8: changes recognition/* model loading lineage — change-log §7 must
         prompt user to re-run training when fixture set grows.
"""

import argparse
import json
import random
import sys
from pathlib import Path

PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ))

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from PIL import Image

FIXTURE_DIR = PROJ / "tests" / "fixtures" / "cards"
MODEL_OUT = PROJ / "models" / "card_cnn.pth"

RANKS = list("23456789TJQKA")  # 13 classes
SUITS = list("shdc")            # 4 classes
RANK_TO_IDX = {r: i for i, r in enumerate(RANKS)}
SUIT_TO_IDX = {s: i for i, s in enumerate(SUITS)}

INPUT_H = 96
INPUT_W = 64
RARE_THRESHOLD = 2  # rank-suit classes with ≤ this many base fixtures get strong-aug + extra loss weight


class CardDataset(Dataset):
    """Loads fixture PNG + JSON pairs; applies augmentation N times per sample.

    Rare-class handling (A + B improvements):
    - rare_set = rank-suit combos with ≤ RARE_THRESHOLD base fixtures
    - Per-sample is_rare flag returned for per-sample loss weighting (A)
    - Strong augmentation transform applied to rare samples in train mode (B)
    """

    def __init__(self, fixture_dir: Path, augmentations: int = 50, train: bool = True):
        self.train = train
        self.samples = []
        for jp in sorted(fixture_dir.glob("*.json")):
            if jp.name.startswith("_"):
                continue
            png = jp.with_suffix(".png")
            if not png.exists():
                continue
            meta = json.loads(jp.read_text(encoding="utf-8"))
            expected = meta.get("expected") or {}
            rank = expected.get("rank")
            suit = expected.get("suit")
            if rank not in RANK_TO_IDX or suit not in SUIT_TO_IDX:
                continue
            self.samples.append((png, rank, suit))

        # Detect rare classes (sample count ≤ threshold) for class-weighted training (A)
        # and stronger augmentation (B).
        counts: dict[str, int] = {}
        for _, r, s in self.samples:
            counts[r + s] = counts.get(r + s, 0) + 1
        self.rare_set = {k for k, v in counts.items() if v <= RARE_THRESHOLD}

        # Replicate each base sample N times so augmentation diversity creates virtual size.
        self.virtual = self.samples * augmentations if train else self.samples

        # Normal augmentation (for well-represented classes)
        self.tf_normal = transforms.Compose([
            transforms.Resize((INPUT_H + 16, INPUT_W + 12)),
            transforms.RandomCrop((INPUT_H, INPUT_W)),
            transforms.RandomRotation(degrees=5, fill=255),
            transforms.ColorJitter(brightness=0.3, contrast=0.2, saturation=0.2, hue=0.03),
            transforms.RandomApply([transforms.GaussianBlur(3, sigma=(0.1, 1.0))], p=0.3),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        # Strong augmentation for rare classes (B): bigger crops, more rotation,
        # stronger color jitter, higher blur prob.
        self.tf_strong = transforms.Compose([
            transforms.Resize((INPUT_H + 24, INPUT_W + 20)),
            transforms.RandomCrop((INPUT_H, INPUT_W)),
            transforms.RandomRotation(degrees=10, fill=255),
            transforms.ColorJitter(brightness=0.5, contrast=0.35, saturation=0.35, hue=0.06),
            transforms.RandomApply([transforms.GaussianBlur(3, sigma=(0.1, 1.5))], p=0.5),
            transforms.RandomApply([transforms.RandomAffine(degrees=0, translate=(0.05, 0.05))], p=0.5),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        # Val/test: no augmentation
        self.tf_val = transforms.Compose([
            transforms.Resize((INPUT_H, INPUT_W)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def __len__(self):
        return len(self.virtual)

    def __getitem__(self, idx):
        png, rank, suit = self.virtual[idx]
        img = Image.open(png).convert("RGB")
        key = rank + suit
        is_rare = 1.0 if key in self.rare_set else 0.0
        if not self.train:
            x = self.tf_val(img)
        elif is_rare:
            x = self.tf_strong(img)
        else:
            x = self.tf_normal(img)
        return x, RANK_TO_IDX[rank], SUIT_TO_IDX[suit], is_rare


class CardCNN(nn.Module):
    """Small two-head CNN. ~200K params."""

    def __init__(self):
        super().__init__()
        self.backbone = nn.Sequential(
            self._block(3, 32),
            self._block(32, 64),
            self._block(64, 128),
            self._block(128, 128),
            nn.AdaptiveAvgPool2d((1, 1)),
            nn.Flatten(),
        )
        self.shared = nn.Sequential(
            nn.Linear(128, 96),
            nn.ReLU(inplace=True),
            nn.Dropout(0.2),
        )
        self.rank_head = nn.Linear(96, len(RANKS))
        self.suit_head = nn.Linear(96, len(SUITS))

    @staticmethod
    def _block(cin, cout):
        return nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1),
            nn.BatchNorm2d(cout),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
        )

    def forward(self, x):
        h = self.backbone(x)
        h = self.shared(h)
        return self.rank_head(h), self.suit_head(h)


def evaluate(model, loader, device, use_amp=False, amp_dtype=None):
    model.eval()
    correct_rank = correct_suit = correct_both = total = 0
    correct_rare_both = total_rare = 0
    if amp_dtype is None:
        amp_dtype = torch.float32
    with torch.no_grad():
        with torch.amp.autocast(device_type=device.type, dtype=amp_dtype, enabled=use_amp):
            for x, yr, ys, ir in loader:
                x = x.to(device, non_blocking=True)
                yr = yr.to(device, non_blocking=True)
                ys = ys.to(device, non_blocking=True)
                ir = ir.to(device, non_blocking=True)
                pr, ps = model(x)
                rr = pr.argmax(1)
                ss = ps.argmax(1)
                both_mask = (rr == yr) & (ss == ys)
                correct_rank += (rr == yr).sum().item()
                correct_suit += (ss == ys).sum().item()
                correct_both += both_mask.sum().item()
                total += yr.size(0)
                # Rare-class breakdown
                rare_mask = ir > 0.5
                correct_rare_both += (both_mask & rare_mask).sum().item()
                total_rare += rare_mask.sum().item()
    rare_acc = (correct_rare_both / total_rare) if total_rare > 0 else None
    return correct_rank / total, correct_suit / total, correct_both / total, rare_acc, total_rare


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--augmentations", type=int, default=50,
                        help="Virtual replication of base fixtures via augmentation")
    parser.add_argument("--batch-size", type=int, default=64,
                        help="Mini-batch size. With small model + GPU can go to 128/256.")
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--val-frac", type=float, default=0.20,
                        help="Fraction of base fixtures held out for validation (non-augmented)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rare-weight", type=float, default=5.0,
                        help=f"Per-sample loss multiplier for rare classes "
                             f"(rank-suit combos with ≤ {RARE_THRESHOLD} base fixtures). "
                             f"1.0 = no extra weight. Default 5.0 = 5× loss on rare samples.")
    parser.add_argument("--num-workers", type=int, default=4,
                        help="DataLoader worker processes for parallel augmentation. "
                             "0 = single-thread (CPU augment blocks GPU). "
                             "4 default fits most GPUs; bump to 8 on beefy CPUs.")
    parser.add_argument("--amp", choices=("auto", "on", "off"), default="auto",
                        help="Mixed-precision training (bf16 on GPU). "
                             "'auto' enables on CUDA, disables on CPU. Speeds GPU 1.5-2×.")
    args = parser.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if device.type == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")

    if not FIXTURE_DIR.exists():
        print(f"✗ Fixture dir not found: {FIXTURE_DIR}", file=sys.stderr)
        return 1

    # Build train/val split from the base (non-augmented) sample list
    base_ds = CardDataset(FIXTURE_DIR, augmentations=1, train=False)
    if len(base_ds.samples) == 0:
        print(f"✗ No fixtures under {FIXTURE_DIR}", file=sys.stderr)
        return 1
    print(f"Base fixtures: {len(base_ds.samples)}")

    indices = list(range(len(base_ds.samples)))
    random.shuffle(indices)
    n_val = max(1, int(len(indices) * args.val_frac))
    val_indices = sorted(indices[:n_val])
    train_indices = sorted(indices[n_val:])
    print(f"Split: train={len(train_indices)} val={len(val_indices)}")

    train_samples = [base_ds.samples[i] for i in train_indices]
    val_samples = [base_ds.samples[i] for i in val_indices]

    train_ds = CardDataset(FIXTURE_DIR, augmentations=args.augmentations, train=True)
    train_ds.samples = train_samples
    train_ds.virtual = train_samples * args.augmentations

    val_ds = CardDataset(FIXTURE_DIR, augmentations=1, train=False)
    val_ds.samples = val_samples
    val_ds.virtual = val_samples

    # GPU utilization tuning
    use_cuda = (device.type == "cuda")
    pin_mem = use_cuda
    persistent = (args.num_workers > 0)
    use_amp = (args.amp == "on") or (args.amp == "auto" and use_cuda)
    amp_dtype = torch.bfloat16 if use_amp else torch.float32
    print(f"GPU tuning: num_workers={args.num_workers}  pin_memory={pin_mem}  "
          f"persistent_workers={persistent}  amp={use_amp} (dtype={amp_dtype})")

    train_loader = DataLoader(
        train_ds, batch_size=args.batch_size, shuffle=True,
        num_workers=args.num_workers, pin_memory=pin_mem,
        persistent_workers=persistent,
    )
    val_loader = DataLoader(
        val_ds, batch_size=args.batch_size, shuffle=False,
        num_workers=args.num_workers, pin_memory=pin_mem,
        persistent_workers=persistent,
    )
    print(f"Train batches: {len(train_loader)} (augmented={len(train_ds)})")
    print(f"Val batches:   {len(val_loader)} (samples={len(val_ds)})")
    rare_in_train = {k for k in train_ds.rare_set if any(r + s == k for _, r, s in train_samples)}
    rare_in_val = {k for k in val_ds.rare_set if any(r + s == k for _, r, s in val_samples)}
    print(f"Rare classes (≤{RARE_THRESHOLD} base fixtures): {len(train_ds.rare_set)} total")
    print(f"  in train split: {sorted(rare_in_train)}")
    print(f"  in val split:   {sorted(rare_in_val)}")
    print(f"Rare-sample loss weight: {args.rare_weight}×")

    model = CardCNN().to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model params: {n_params:,}")

    opt = optim.Adam(model.parameters(), lr=args.lr)
    sched = optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    # reduction='none' so we can multiply per-sample by rare-weight before averaging.
    criterion = nn.CrossEntropyLoss(reduction='none')

    best_val = 0.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        loss_acc = 0.0
        n = 0
        for x, yr, ys, ir in train_loader:
            x = x.to(device, non_blocking=True)
            yr = yr.to(device, non_blocking=True)
            ys = ys.to(device, non_blocking=True)
            ir = ir.to(device, non_blocking=True)
            with torch.amp.autocast(device_type=device.type, dtype=amp_dtype, enabled=use_amp):
                pr, ps = model(x)
                # Per-sample loss * rare weight (rare samples get amplified gradient).
                sample_w = 1.0 + (args.rare_weight - 1.0) * ir
                loss_r = (criterion(pr, yr) * sample_w).mean()
                loss_s = (criterion(ps, ys) * sample_w).mean()
                loss = loss_r + loss_s
            opt.zero_grad()
            loss.backward()
            opt.step()
            loss_acc += loss.item() * x.size(0)
            n += x.size(0)
        sched.step()
        train_loss = loss_acc / max(1, n)
        rank_acc, suit_acc, both_acc, rare_acc, n_rare = evaluate(model, val_loader, device, use_amp, amp_dtype)
        marker = ""
        if both_acc > best_val:
            best_val = both_acc
            MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "state_dict": model.state_dict(),
                "ranks": RANKS,
                "suits": SUITS,
                "input_h": INPUT_H,
                "input_w": INPUT_W,
                "val_both_acc": best_val,
            }, MODEL_OUT)
            marker = "  ✓ saved"
        rare_str = f" rare={rare_acc:.2%}({n_rare})" if rare_acc is not None else ""
        print(f"Epoch {epoch:3d}/{args.epochs}  "
              f"train_loss={train_loss:.4f}  "
              f"val rank={rank_acc:.2%} suit={suit_acc:.2%} both={both_acc:.2%}{rare_str}{marker}")

    print(f"\nBest val both-acc: {best_val:.2%}")
    print(f"Saved: {MODEL_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
