"""Data access layer — bridges domain models to SQLAlchemy ORM."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from events.models import ActionEvent, Hand
from storage.models import ActionEventModel, HandModel


class HandRepository:
    """CRUD for Hand domain objects."""

    def create(self, session: Session, hand: Hand) -> HandModel:
        model = HandModel(
            id=hand.id,
            table_name=hand.table_name,
            game_type=hand.game_type,
            stakes=hand.stakes,
            hero_name=hand.hero_name,
            hero_position=hand.hero_position.value if hand.hero_position else None,
            hero_cards=hand.hero_cards,
            started_at=hand.started_at or datetime.now(timezone.utc),
            raw_data=hand.raw_data,
        )
        session.add(model)
        session.commit()
        return model

    def update(self, session: Session, hand: Hand) -> HandModel:
        model = session.get(HandModel, hand.id)
        if model is None:
            raise ValueError(f"Hand {hand.id} not found")
        model.community_cards = {k.value: v for k, v in hand.community_cards.items()}
        model.seats = hand.seats
        model.ended_at = hand.ended_at
        model.result = hand.result
        model.raw_data = hand.raw_data
        model.pot_size_final = hand.pot_size_final
        session.commit()
        return model

    def get(self, session: Session, hand_id: UUID) -> HandModel | None:
        return session.get(HandModel, hand_id)


class ActionEventRepository:
    """CRUD for ActionEvent domain objects."""

    def create(self, session: Session, event: ActionEvent) -> ActionEventModel:
        model = ActionEventModel(
            id=event.id,
            hand_id=event.hand_id,
            player_name=event.player_name,
            position=event.position.value,
            street=event.street.value,
            action_type=event.action_type.value,
            sequence_number=event.sequence_number,
            amount=event.amount,
            facing_action=event.facing_action,
            effective_stack_bb=event.effective_stack_bb,
            pot_size_bb=event.pot_size_bb,
            players_in_pot=event.players_in_pot,
            board_texture=event.board_texture,
            timestamp=event.timestamp or datetime.now(timezone.utc),
            raw_data=event.raw_data,
            confidence_score=event.confidence_score,
        )
        session.add(model)
        session.commit()
        return model

    def get_for_hand(self, session: Session, hand_id: UUID) -> list[ActionEventModel]:
        return (
            session.query(ActionEventModel)
            .filter(ActionEventModel.hand_id == hand_id)
            .order_by(ActionEventModel.sequence_number)
            .all()
        )
