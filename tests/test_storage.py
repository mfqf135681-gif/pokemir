"""Storage layer integration tests — requires a reachable PostgreSQL instance.

Skipped automatically when the database cannot be connected to.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from events.models import ActionEvent, ActionType, Hand, Position, Street


@pytest.fixture(scope="module")
def db_session():
    """Yield a DB session; skip the whole module if PostgreSQL is unreachable."""
    try:
        from storage.database import SessionLocal, init_db
        init_db()
        session = SessionLocal()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    try:
        yield session
    finally:
        session.close()


def test_hand_create_and_read(db_session):
    from storage.repository import HandRepository

    repo = HandRepository()
    hand = Hand(
        table_name="test_table",
        game_type="NLH",
        stakes="0.05/0.10",
        hero_name="Hero",
        hero_position=Position.CO,
        hero_cards=["Ah", "Kd"],
    )
    model = repo.create(db_session, hand)
    try:
        retrieved = repo.get(db_session, hand.id)
        assert retrieved is not None
        assert retrieved.table_name == "test_table"
        assert retrieved.hero_cards == ["Ah", "Kd"]
    finally:
        db_session.delete(model)
        db_session.commit()


def test_action_events_roundtrip(db_session):
    from storage.repository import ActionEventRepository, HandRepository

    hand_repo = HandRepository()
    event_repo = ActionEventRepository()

    hand = Hand(table_name="test_table", hero_position=Position.CO)
    model = hand_repo.create(db_session, hand)

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
    try:
        for e in events:
            event_repo.create(db_session, e)

        db_session.expire_all()
        saved = event_repo.get_for_hand(db_session, hand.id)
        assert len(saved) == 3
        assert saved[0].action_type == "post_sb"
        assert saved[1].action_type == "post_bb"
        assert saved[2].action_type == "raise"
    finally:
        db_session.delete(model)
        db_session.commit()


def test_hand_update_community_cards(db_session):
    from storage.repository import HandRepository

    repo = HandRepository()
    hand = Hand(table_name="test_table", hero_position=Position.CO)
    model = repo.create(db_session, hand)

    try:
        hand.community_cards = {Street.FLOP: ["Ah", "Kh", "Qh"]}
        repo.update(db_session, hand)
        db_session.expire_all()
        updated = repo.get(db_session, hand.id)
        assert updated.community_cards == {"flop": ["Ah", "Kh", "Qh"]}
    finally:
        db_session.delete(model)
        db_session.commit()
