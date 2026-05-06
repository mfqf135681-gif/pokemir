"""Test database storage layer — CRUD for Hand and ActionEvent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from events.models import ActionEvent, ActionType, Hand, Position, Street
from storage.database import SessionLocal, init_db
from storage.repository import ActionEventRepository, HandRepository


def main():
    print("=" * 50)
    print("Storage Layer Tests")
    print("=" * 50 + "\n")

    # Ensure tables exist
    print("Initializing database...")
    init_db()
    print("Tables ready.\n")

    db = SessionLocal()

    hand_repo = HandRepository()
    event_repo = ActionEventRepository()

    # ── Create a hand ────────────────────────────────────
    hand = Hand(
        table_name="test_table",
        game_type="NLH",
        stakes="0.05/0.10",
        hero_name="Hero",
        hero_position=Position.CO,
        hero_cards=["Ah", "Kd"],
    )
    model = hand_repo.create(db, hand)
    print(f"Created hand: {model.id}")

    # Read it back
    retrieved = hand_repo.get(db, hand.id)
    assert retrieved is not None, "Hand not found!"
    assert retrieved.table_name == "test_table"
    print(f"Read hand back: {retrieved.table_name} — hero: {retrieved.hero_cards}")

    # ── Create action events ─────────────────────────────
    events = [
        ActionEvent(
            hand_id=hand.id,
            player_name="Player_0",
            position=Position.BTN,
            street=Street.PREFLOP,
            action_type=ActionType.POST_SB,
            sequence_number=1,
            amount=0.05,
        ),
        ActionEvent(
            hand_id=hand.id,
            player_name="Player_1",
            position=Position.BB,
            street=Street.PREFLOP,
            action_type=ActionType.POST_BB,
            sequence_number=2,
            amount=0.10,
        ),
        ActionEvent(
            hand_id=hand.id,
            player_name="Hero",
            position=Position.CO,
            street=Street.PREFLOP,
            action_type=ActionType.RAISE,
            sequence_number=3,
            amount=0.30,
            facing_action="BB 0.10",
        ),
    ]
    for e in events:
        event_repo.create(db, e)
    print(f"Created {len(events)} action events")

    # Read events back
    db.expire_all()  # force re-read
    saved = event_repo.get_for_hand(db, hand.id)
    print(f"Read {len(saved)} events back:")
    for s in saved:
        print(f"  #{s.sequence_number}: {s.player_name}({s.position}) "
              f"{s.action_type} {s.amount or ''}")

    assert len(saved) == 3, f"Expected 3 events, got {len(saved)}"
    assert saved[0].action_type == "post_sb"
    assert saved[1].action_type == "post_bb"
    assert saved[2].action_type == "raise"

    # ── Update hand ──────────────────────────────────────
    hand.community_cards = {Street.FLOP: ["Ah", "Kh", "Qh"]}
    hand_repo.update(db, hand)
    db.expire_all()
    updated = hand_repo.get(db, hand.id)
    assert updated.community_cards == {"flop": ["Ah", "Kh", "Qh"]}
    print(f"\nUpdated community cards: {updated.community_cards}")

    # ── Cleanup ──────────────────────────────────────────
    db.delete(model)  # cascade deletes action_events
    db.commit()
    db.close()

    print("\n" + "=" * 50)
    print("Storage tests: ALL PASSED")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
