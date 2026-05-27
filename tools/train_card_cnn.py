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
import io
import json
import random
import sys
from pathlib import Path

PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ))

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from PIL import Image

# Multi-domain fixture roots (#4 multi-domain val + #6 NONCARD):
#   cards/                       — community / hero cards (clean, frontal); domain="community"
#   showdown/<rank><suit>/       — opponent showdown reveal (smaller, slight angle); domain="showdown"
#   showdown_noncard/            — avatar/garbage that CNN was tempted to misread; domain="noncard"
FIXTURE_DIR = PROJ / "tests" / "fixtures" / "cards"
SHOWDOWN_DIR = PROJ / "tests" / "fixtures" / "showdown"
NONCARD_DIR = PROJ / "tests" / "fixtures" / "showdown_noncard"
MODEL_OUT = PROJ / "models" / "card_cnn.pth"

RANKS = list("23456789TJQKA")  # 13 classes
SUITS = list("shdc")            # 4 classes
RANK_TO_IDX = {r: i for i, r in enumerate(RANKS)}
SUIT_TO_IDX = {s: i for i, s in enumerate(SUITS)}

# Domain enum — index into [community, showdown, noncard]
DOMAINS = ("community", "showdown", "noncard")
DOMAIN_TO_IDX = {d: i for i, d in enumerate(DOMAINS)}

INPUT_H = 96
INPUT_W = 64
RARE_THRESHOLD = 2  # rank-suit classes with ≤ this many base fixtures get strong-aug + extra loss weight


def _jpeg_noise(img: Image.Image, quality: int = 70) -> Image.Image:
    """Round-trip through JPEG encoder to add compression artefacts.

    WePoker H5 UI is heavily compressed; injecting this in training narrows
    the domain gap between clean fixture PNGs and live in-game captures.
    """
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


class _RandomJpegNoise:
    """Pickle-able transform wrapper (lambdas don't survive DataLoader workers)."""

    def __init__(self, p: float = 0.4, quality_range=(55, 90)):
        self.p = p
        self.q_lo, self.q_hi = quality_range

    def __call__(self, img):
        if random.random() < self.p:
            return _jpeg_noise(img, random.randint(self.q_lo, self.q_hi))
        return img


class CardDataset(Dataset):
    """Loads multi-domain fixture set.

    Samples carry domain ∈ {community, showdown, noncard}:
    - community: from cards/*.json  — rank/suit from "expected" key
    - showdown:  from showdown/<rank><suit>/*.png  — label encoded in dir name
    - noncard:   from showdown_noncard/*.png  — no rank/suit (placeholders 0; loss masked)

    Returns per-sample: (x, rank_idx, suit_idx, is_rare, domain_idx, is_card)
    Rare/strong aug + per-sample loss weighting unchanged.
    Showdown samples additionally pass through perspective transform (slight angle
    seen in real showdown reveals);noncard samples get medium aug to avoid overfit
    to specific avatars.
    """

    def __init__(self, fixture_dir: Path = FIXTURE_DIR,
                 showdown_dir: Path = SHOWDOWN_DIR,
                 noncard_dir: Path = NONCARD_DIR,
                 augmentations: int = 50, train: bool = True):
        self.train = train
        self.samples: list[tuple[Path, str | None, str | None, str]] = []  # (png, rank, suit, domain)

        # 1) Community fixtures (legacy path)
        if fixture_dir.exists():
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
                self.samples.append((png, rank, suit, "community"))

        # 2) Showdown fixtures (from tools/label_showdown.py)
        if showdown_dir.exists():
            for card_dir in sorted(showdown_dir.iterdir()):
                if not card_dir.is_dir() or len(card_dir.name) != 2:
                    continue
                rank, suit = card_dir.name[0], card_dir.name[1]
                if rank not in RANK_TO_IDX or suit not in SUIT_TO_IDX:
                    continue
                for png in sorted(card_dir.glob("*.png")):
                    self.samples.append((png, rank, suit, "showdown"))

        # 3) Noncard fixtures (NONCARD negative class) — placeholders, masked from rank/suit loss
        if noncard_dir.exists():
            for png in sorted(noncard_dir.glob("*.png")):
                self.samples.append((png, None, None, "noncard"))

        # Rare detection (cards only; noncards never rare-flagged)
        counts: dict[str, int] = {}
        for _, r, s, dom in self.samples:
            if dom == "noncard":
                continue
            counts[r + s] = counts.get(r + s, 0) + 1
        self.rare_set = {k for k, v in counts.items() if v <= RARE_THRESHOLD}

        # Replicate each base sample N times so augmentation diversity creates virtual size.
        self.virtual = self.samples * augmentations if train else self.samples

        # Normal augmentation (for well-represented community classes)
        self.tf_normal = transforms.Compose([
            transforms.Resize((INPUT_H + 16, INPUT_W + 12)),
            transforms.RandomCrop((INPUT_H, INPUT_W)),
            transforms.RandomRotation(degrees=5, fill=255),
            transforms.ColorJitter(brightness=0.3, contrast=0.2, saturation=0.2, hue=0.03),
            transforms.RandomApply([transforms.GaussianBlur(3, sigma=(0.1, 1.0))], p=0.3),
            _RandomJpegNoise(p=0.4),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        # Strong augmentation for rare classes
        self.tf_strong = transforms.Compose([
            transforms.Resize((INPUT_H + 24, INPUT_W + 20)),
            transforms.RandomCrop((INPUT_H, INPUT_W)),
            transforms.RandomRotation(degrees=10, fill=255),
            transforms.ColorJitter(brightness=0.5, contrast=0.35, saturation=0.35, hue=0.06),
            transforms.RandomApply([transforms.GaussianBlur(3, sigma=(0.1, 1.5))], p=0.5),
            transforms.RandomApply([transforms.RandomAffine(degrees=0, translate=(0.05, 0.05))], p=0.5),
            _RandomJpegNoise(p=0.5),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        # Showdown augmentation: same noise budget + perspective (real reveals have slight tilt)
        self.tf_showdown = transforms.Compose([
            transforms.Resize((INPUT_H + 16, INPUT_W + 12)),
            transforms.RandomCrop((INPUT_H, INPUT_W)),
            transforms.RandomPerspective(distortion_scale=0.12, p=0.5, fill=255),
            transforms.RandomRotation(degrees=8, fill=255),
            transforms.ColorJitter(brightness=0.35, contrast=0.25, saturation=0.25, hue=0.04),
            transforms.RandomApply([transforms.GaussianBlur(3, sigma=(0.1, 1.2))], p=0.4),
            _RandomJpegNoise(p=0.5),
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
        png, rank, suit, domain = self.virtual[idx]
        img = Image.open(png).convert("RGB")
        is_card = 0.0 if domain == "noncard" else 1.0
        key = (rank + suit) if (rank and suit) else ""
        is_rare = 1.0 if key in self.rare_set else 0.0
        if not self.train:
            x = self.tf_val(img)
        elif domain == "showdown":
            x = self.tf_showdown(img)
        elif is_rare:
            x = self.tf_strong(img)
        else:
            x = self.tf_normal(img)
        rank_idx = RANK_TO_IDX[rank] if rank else 0  # placeholder; loss masked when is_card=0
        suit_idx = SUIT_TO_IDX[suit] if suit else 0
        return x, rank_idx, suit_idx, is_rare, DOMAIN_TO_IDX[domain], is_card


class CardCNN(nn.Module):
    """Small three-head CNN. ~200K params.

    Heads:
    - rank_head: 13 classes (2-9, T, J, Q, K, A)
    - suit_head: 4 classes (s, h, d, c)
    - iscard_head: 2 classes (NONCARD, CARD) — #6 anti-hallucination gate

    Inference protocol: if iscard_head says NONCARD → return None (don't fall
    through to a confused rank/suit prediction). Backward-compat: old ckpts
    without iscard_head still load (CnnClassifier skips iscard gate).
    """

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
        self.iscard_head = nn.Linear(96, 2)  # 0=NONCARD, 1=CARD

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
        return self.rank_head(h), self.suit_head(h), self.iscard_head(h)


def evaluate(model, loader, device, use_amp=False, amp_dtype=None):
    """Per-domain eval (#4) + NONCARD recall (#6).

    Returns dict with keys per domain:
      rank_acc, suit_acc, both_acc, rare_acc(community-only sense), n
    plus noncard_recall (frac of noncard samples predicted as NONCARD by iscard head).
    """
    model.eval()
    if amp_dtype is None:
        amp_dtype = torch.float32

    per_domain: dict[str, dict] = {d: {"rank": 0, "suit": 0, "both": 0, "n": 0,
                                        "rare_both": 0, "rare_n": 0}
                                    for d in DOMAINS}
    noncard_pred_noncard = 0
    card_pred_card = 0
    card_pred_total = 0  # all is_card=1 samples seen

    with torch.no_grad():
        with torch.amp.autocast(device_type=device.type, dtype=amp_dtype, enabled=use_amp):
            for x, yr, ys, ir, dom, ic in loader:
                x = x.to(device, non_blocking=True)
                yr = yr.to(device, non_blocking=True)
                ys = ys.to(device, non_blocking=True)
                ir = ir.to(device, non_blocking=True)
                dom = dom.to(device, non_blocking=True)
                ic = ic.to(device, non_blocking=True)
                pr, ps, pic = model(x)
                rr = pr.argmax(1)
                ss = ps.argmax(1)
                ic_pred = pic.argmax(1)  # 0=NONCARD, 1=CARD
                both_mask = (rr == yr) & (ss == ys)
                rare_mask = ir > 0.5
                card_mask = ic > 0.5

                # iscard binary recall
                noncard_pred_noncard += ((~card_mask) & (ic_pred == 0)).sum().item()
                card_pred_card += (card_mask & (ic_pred == 1)).sum().item()
                card_pred_total += card_mask.sum().item()

                # Per-domain rank/suit accuracy (only for card samples)
                for d_idx, d_name in enumerate(DOMAINS):
                    if d_name == "noncard":
                        continue
                    mask = (dom == d_idx) & card_mask
                    n = mask.sum().item()
                    if n == 0:
                        continue
                    per_domain[d_name]["n"] += n
                    per_domain[d_name]["rank"] += ((rr == yr) & mask).sum().item()
                    per_domain[d_name]["suit"] += ((ss == ys) & mask).sum().item()
                    per_domain[d_name]["both"] += (both_mask & mask).sum().item()
                    rare_n = (rare_mask & mask).sum().item()
                    if rare_n > 0:
                        per_domain[d_name]["rare_n"] += rare_n
                        per_domain[d_name]["rare_both"] += (both_mask & rare_mask & mask).sum().item()

    # Per-domain rank/suit accuracy output
    out = {}
    for d in ("community", "showdown"):
        s = per_domain[d]
        n = s["n"]
        if n == 0:
            out[d] = None
            continue
        out[d] = {
            "rank_acc": s["rank"] / n,
            "suit_acc": s["suit"] / n,
            "both_acc": s["both"] / n,
            "rare_acc": (s["rare_both"] / s["rare_n"]) if s["rare_n"] > 0 else None,
            "n": n, "rare_n": s["rare_n"],
        }
    # iscard head: NONCARD recall = TP / (TP + FN); count noncards by domain tag.
    total_noncard = sum(1 for _, _, _, dom in loader.dataset.virtual if dom == "noncard")
    out["noncard_recall"] = (noncard_pred_noncard / total_noncard) if total_noncard > 0 else None
    out["card_recall"] = (card_pred_card / card_pred_total) if card_pred_total > 0 else None
    return out


def calibrate_temperature(model: CardCNN, loader, device, use_amp: bool, amp_dtype):
    """Post-training temperature scaling (#5).

    Learn one scalar T per head on val logits so that softmax(z / T) is well-
    calibrated: predicted probability = empirical accuracy. Uses L-BFGS to
    minimize NLL with respect to T. Returns dict {rank: T_r, suit: T_s, iscard: T_i}.

    Why per-head: rank tends to be over-confident, suit is already calibrated
    (saw rank_conf 0.55 / suit_conf 0.98 mismatch in production). Per-head T fixes.

    Only computed when there are val samples for each head's target distribution.
    Falls back to T=1.0 (no rescaling) when val set is degenerate.
    """
    model.eval()
    # Gather logits + labels on val set (no aug, no dropout)
    rank_logits, rank_labels = [], []
    suit_logits, suit_labels = [], []
    ic_logits, ic_labels = [], []
    with torch.no_grad():
        with torch.amp.autocast(device_type=device.type, dtype=amp_dtype, enabled=use_amp):
            for x, yr, ys, _ir, _dom, ic in loader:
                x = x.to(device); yr = yr.to(device); ys = ys.to(device); ic = ic.to(device)
                pr, ps, pic = model(x)
                # Only learn rank/suit T on card samples (noncard has placeholder labels)
                card_mask = ic > 0.5
                if card_mask.any():
                    rank_logits.append(pr[card_mask].float())
                    rank_labels.append(yr[card_mask])
                    suit_logits.append(ps[card_mask].float())
                    suit_labels.append(ys[card_mask])
                ic_logits.append(pic.float())
                ic_labels.append(ic.long())

    def _fit(logits, labels):
        if not logits:
            return 1.0
        z = torch.cat(logits).to(device)
        y = torch.cat(labels).to(device)
        if z.numel() == 0 or y.numel() == 0:
            return 1.0
        T = torch.nn.Parameter(torch.ones(1, device=device))
        opt = torch.optim.LBFGS([T], lr=0.1, max_iter=50)
        def closure():
            opt.zero_grad()
            loss = F.cross_entropy(z / T.clamp(min=1e-3), y)
            loss.backward()
            return loss
        opt.step(closure)
        return float(T.detach().clamp(min=1e-3).item())

    return {
        "rank": _fit(rank_logits, rank_labels),
        "suit": _fit(suit_logits, suit_labels),
        "iscard": _fit(ic_logits, ic_labels),
    }


def _best_score(domain_acc: dict) -> float:
    """Combined val score for best-checkpoint selection (#4).

    Uses harmonic mean of community + showdown both_acc when both present, so
    a regression on either domain pulls down the score (vs simple sum which can
    mask domain-specific collapse). Falls back to community-only when showdown
    has zero val samples (legacy / pre-recording state).
    """
    c = domain_acc.get("community")
    s = domain_acc.get("showdown")
    c_acc = c["both_acc"] if c else 0.0
    s_acc = s["both_acc"] if s else None
    if s_acc is None:
        return c_acc  # no showdown val yet — single-domain best
    if c_acc <= 0 or s_acc <= 0:
        return 0.0
    return 2 * c_acc * s_acc / (c_acc + s_acc)  # harmonic mean


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

    # Build per-domain train/val split.  Stratify by domain so showdown samples
    # (smaller pool) still get a val slice and aren't all swallowed into train.
    base_ds = CardDataset(augmentations=1, train=False)
    if len(base_ds.samples) == 0:
        print(f"✗ No fixtures under {FIXTURE_DIR} / {SHOWDOWN_DIR} / {NONCARD_DIR}", file=sys.stderr)
        return 1
    by_domain: dict[str, list] = {d: [] for d in DOMAINS}
    for s in base_ds.samples:
        by_domain[s[3]].append(s)
    print(f"Base fixtures by domain:")
    for d in DOMAINS:
        print(f"  {d}: {len(by_domain[d])}")

    train_samples: list = []
    val_samples: list = []
    for d in DOMAINS:
        dom_samples = by_domain[d]
        if not dom_samples:
            continue
        random.shuffle(dom_samples)
        n_val_dom = max(1, int(len(dom_samples) * args.val_frac)) if len(dom_samples) >= 5 else 0
        val_samples.extend(dom_samples[:n_val_dom])
        train_samples.extend(dom_samples[n_val_dom:])
    random.shuffle(train_samples)
    val_samples.sort(key=lambda s: (s[3], str(s[0])))
    print(f"Split: train={len(train_samples)} val={len(val_samples)}")

    train_ds = CardDataset(augmentations=args.augmentations, train=True)
    train_ds.samples = train_samples
    train_ds.virtual = train_samples * args.augmentations

    val_ds = CardDataset(augmentations=1, train=False)
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
    rare_in_train = {k for k in train_ds.rare_set if any((r and s and r + s == k) for _, r, s, _ in train_samples)}
    rare_in_val = {k for k in val_ds.rare_set if any((r and s and r + s == k) for _, r, s, _ in val_samples)}
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

    best_score = 0.0
    best_domain_acc: dict = {}
    for epoch in range(1, args.epochs + 1):
        model.train()
        loss_acc = 0.0
        n = 0
        for x, yr, ys, ir, _dom, ic in train_loader:
            x = x.to(device, non_blocking=True)
            yr = yr.to(device, non_blocking=True)
            ys = ys.to(device, non_blocking=True)
            ir = ir.to(device, non_blocking=True)
            ic = ic.to(device, non_blocking=True)
            with torch.amp.autocast(device_type=device.type, dtype=amp_dtype, enabled=use_amp):
                pr, ps, pic = model(x)
                # Per-sample loss * rare weight (rare samples get amplified gradient).
                sample_w = 1.0 + (args.rare_weight - 1.0) * ir
                # rank/suit loss only on card samples — noncard targets are placeholders.
                card_mask = (ic > 0.5).float()
                # (rank_loss + suit_loss) * sample_weight * card_mask, normalize by card count.
                loss_r_raw = criterion(pr, yr) * sample_w * card_mask
                loss_s_raw = criterion(ps, ys) * sample_w * card_mask
                n_cards = card_mask.sum().clamp(min=1.0)
                loss_r = loss_r_raw.sum() / n_cards
                loss_s = loss_s_raw.sum() / n_cards
                # iscard binary loss applies to ALL samples
                loss_ic = criterion(pic, ic.long()).mean()
                loss = loss_r + loss_s + loss_ic
            opt.zero_grad()
            loss.backward()
            opt.step()
            loss_acc += loss.item() * x.size(0)
            n += x.size(0)
        sched.step()
        train_loss = loss_acc / max(1, n)
        domain_acc = evaluate(model, val_loader, device, use_amp, amp_dtype)
        score = _best_score(domain_acc)
        marker = ""
        if score > best_score:
            best_score = score
            best_domain_acc = domain_acc
            MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "state_dict": model.state_dict(),
                "ranks": RANKS,
                "suits": SUITS,
                "input_h": INPUT_H,
                "input_w": INPUT_W,
                "val_both_acc": domain_acc["community"]["both_acc"] if domain_acc.get("community") else None,
                "val_showdown_both_acc": domain_acc["showdown"]["both_acc"] if domain_acc.get("showdown") else None,
                "val_noncard_recall": domain_acc.get("noncard_recall"),
                "val_card_recall": domain_acc.get("card_recall"),
                "val_combined_score": best_score,
                # Temperatures filled after training (post-calibration save below)
                "temperature": {"rank": 1.0, "suit": 1.0, "iscard": 1.0},
            }, MODEL_OUT)
            marker = "  ✓ saved"
        c = domain_acc.get("community"); s = domain_acc.get("showdown")
        line = f"Epoch {epoch:3d}/{args.epochs}  loss={train_loss:.4f}  "
        if c:
            line += f"comm both={c['both_acc']:.2%}({c['n']}) "
        if s:
            line += f"sd both={s['both_acc']:.2%}({s['n']}) "
        if domain_acc.get("noncard_recall") is not None:
            line += f"nc_rec={domain_acc['noncard_recall']:.2%} "
        line += f"score={score:.4f}{marker}"
        print(line)

    print(f"\nBest combined val score (harmonic mean of community+showdown both_acc): {best_score:.4f}")
    if best_domain_acc.get("community"):
        print(f"  community both_acc: {best_domain_acc['community']['both_acc']:.2%}")
    if best_domain_acc.get("showdown"):
        print(f"  showdown  both_acc: {best_domain_acc['showdown']['both_acc']:.2%}")
    if best_domain_acc.get("noncard_recall") is not None:
        print(f"  noncard recall:     {best_domain_acc['noncard_recall']:.2%}")

    # Reload best checkpoint then calibrate temperatures on val set (#5)
    print("\nLoading best checkpoint for temperature calibration...")
    ckpt = torch.load(MODEL_OUT, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["state_dict"])
    T = calibrate_temperature(model, val_loader, device, use_amp, amp_dtype)
    print(f"Learned temperatures: rank={T['rank']:.3f}  suit={T['suit']:.3f}  iscard={T['iscard']:.3f}")
    ckpt["temperature"] = T
    torch.save(ckpt, MODEL_OUT)
    print(f"Saved: {MODEL_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
