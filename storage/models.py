"""SQLAlchemy ORM models — maps to the PostgreSQL schema in contracts/models.sql."""

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class HandModel(Base):
    __tablename__ = "hands"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hand_number = Column(Integer)
    table_name = Column(Text, nullable=False)
    game_type = Column(Text, nullable=False, default="NLH")
    stakes = Column(Text, nullable=False, default="0.00/0.00")
    hero_name = Column(Text)
    hero_position = Column(Text)
    hero_cards = Column(JSONB)
    community_cards = Column(JSONB)
    seats = Column(JSONB)
    started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))
    result = Column(JSONB)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default="now()")

    action_events = relationship(
        "ActionEventModel", back_populates="hand",
        cascade="all, delete-orphan",
    )


class ActionEventModel(Base):
    __tablename__ = "action_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hand_id = Column(UUID(as_uuid=True), ForeignKey("hands.id", ondelete="CASCADE"), nullable=False)
    player_name = Column(Text, nullable=False)
    position = Column(Text, nullable=False)
    street = Column(Text, nullable=False)
    action_type = Column(Text, nullable=False)
    sequence_number = Column(Integer, nullable=False)
    amount = Column(Float)
    facing_action = Column(Text)
    effective_stack_bb = Column(Float)
    pot_size_bb = Column(Float)
    players_in_pot = Column(Integer, default=0)
    board_texture = Column(JSONB)
    timestamp = Column(DateTime(timezone=True))
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), server_default="now()")

    hand = relationship("HandModel", back_populates="action_events")
