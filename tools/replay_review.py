"""Interactive CLI to review low-confidence action_events and write corrections.

P4 Layer 4 of the 4-layer cross-validation pyramid. Use after pipeline has been
running and accumulated some events; pulls events with confidence_score below
a threshold and lets the user accept/skip/correct each one. Corrections are
written to the replay_corrections table.

Usage:
    python tools/replay_review.py [--threshold 0.7] [--limit 20] [--name <profile>]

Keys at each prompt:
    y / Enter   accept as-is (no correction)
    n / s       skip this event (don't write correction; show next)
    a           override action_type (will prompt for new value)
    m           override amount (will prompt for new number)
    q           quit
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

# Add project root to sys.path so we can import config/storage/events
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402

VALID_ACTION_TYPES = (
    "fold", "check", "call", "bet", "raise", "all_in",
    "post_sb", "post_bb", "post_ante",
)


def _connect():
    """Open a psycopg2 connection from POKEMIR_DB_PASSWORD env (loaded from .env)."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    return psycopg2.connect(
        host=os.environ.get("POKEMIR_DB_HOST", "localhost"),
        port=int(os.environ.get("POKEMIR_DB_PORT", "5432")),
        user=os.environ.get("POKEMIR_DB_USER", "poker_user"),
        password=os.environ["POKEMIR_DB_PASSWORD"],
        dbname=os.environ.get("POKEMIR_DB_NAME", "poker_assistant"),
    )


def _fetch_events(conn, threshold: float, limit: int):
    cur = conn.cursor()
    cur.execute("""
        SELECT ae.id, ae.hand_id, ae.player_name, ae.position, ae.street,
               ae.action_type, ae.amount, ae.confidence_score, ae.raw_data,
               ae.timestamp
        FROM action_events ae
        WHERE ae.confidence_score < %s
        ORDER BY ae.timestamp DESC
        LIMIT %s
    """, (threshold, limit))
    return cur.fetchall()


def _write_correction(conn, hand_id, event_id, ctype, original, corrected, notes=""):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO replay_corrections
        (id, hand_id, event_id, correction_type, original_value, corrected_value, notes)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
    """, (
        uuid.uuid4(),
        hand_id, event_id, ctype,
        json.dumps(original), json.dumps(corrected),
        notes,
    ))
    conn.commit()


def main():
    p = argparse.ArgumentParser(description="Review low-confidence action events")
    p.add_argument("--threshold", type=float, default=0.7,
                   help="confidence_score < threshold are listed (default: 0.7)")
    p.add_argument("--limit", type=int, default=20, help="max events to review")
    args = p.parse_args()

    conn = _connect()
    rows = _fetch_events(conn, args.threshold, args.limit)
    if not rows:
        print(f"No events with confidence_score < {args.threshold}. ✓")
        return 0

    print(f"Reviewing {len(rows)} events (confidence < {args.threshold})\n")
    print("Keys: [y] accept / [n] skip / [a] change action / [m] change amount / [q] quit\n")

    for r in rows:
        (eid, hid, pname, pos, street, atype, amount, conf, raw, ts) = r
        print("─" * 60)
        print(f"  {ts:%H:%M:%S}  {pname:15s} {pos:5s} {street:8s} "
              f"{atype:8s} amt={amount}  conf={conf}")
        if raw:
            print(f"  raw_data: stack_d={raw.get('stack_delta')} "
                  f"pot_d={raw.get('pot_delta')} "
                  f"text={raw.get('action_text')!r}")
            if raw.get("override_reason"):
                print(f"  P3 override: {raw['override_reason']}")
        choice = input("  [y/n/a/m/q] > ").strip().lower() or "y"
        if choice == "q":
            break
        if choice in ("n", "s", "y"):
            continue
        if choice == "a":
            new = input(f"  new action_type ({'/'.join(VALID_ACTION_TYPES)}) > ").strip().lower()
            if new in VALID_ACTION_TYPES:
                _write_correction(conn, hid, eid, "action_type", atype, new,
                                  notes=f"reviewer override; original conf={conf}")
                print(f"  ✓ correction written: {atype} → {new}")
            else:
                print(f"  invalid action_type {new!r}, skipped.")
        elif choice == "m":
            try:
                new = float(input("  new amount (float) > ").strip())
                _write_correction(conn, hid, eid, "amount", amount, new,
                                  notes=f"reviewer override; original conf={conf}")
                print(f"  ✓ correction written: {amount} → {new}")
            except ValueError:
                print("  invalid amount, skipped.")
    print("\nReview session done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
