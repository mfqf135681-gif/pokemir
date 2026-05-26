"""SQLAlchemy ORM models — maps to the PostgreSQL schema in contracts/models.sql."""

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


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
    pot_size_final = Column(Float)
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
    confidence_score = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default="now()")

    hand = relationship("HandModel", back_populates="action_events")


class PlayerStatsCacheModel(Base):
    __tablename__ = "player_stats_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_name = Column(Text, nullable=False, unique=True)
    total_hands = Column(Integer, nullable=False, server_default="0")

    vpip = Column(Float)
    pfr = Column(Float)
    af = Column(Float)
    three_bet_pct = Column(Float)
    fold_to_three_bet_pct = Column(Float)
    ats = Column(Float)
    call_open_pct = Column(Float)

    cbet_pct = Column(Float)
    fold_to_cbet_pct = Column(Float)
    raise_cbet_pct = Column(Float)
    wtsd_pct = Column(Float)
    wsd_pct = Column(Float)
    double_barrel_pct = Column(Float)
    check_raise_pct = Column(Float)
    donk_bet_pct = Column(Float)

    last_updated = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    stats_json = Column(JSONB)


class PlayerSituationalStatsModel(Base):
    __tablename__ = "player_situational_stats"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_name = Column(Text, nullable=False)
    stat_type = Column(Text, nullable=False)
    dimensions = Column(JSONB, nullable=False)
    stat_value = Column(JSONB, nullable=False)
    sample_size = Column(Integer, nullable=False, server_default="0")
    last_updated = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ReplayCorrectionModel(Base):
    __tablename__ = "replay_corrections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hand_id = Column(UUID(as_uuid=True), ForeignKey("hands.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(UUID(as_uuid=True), ForeignKey("action_events.id", ondelete="SET NULL"))

    correction_type = Column(Text, nullable=False)
    original_value = Column(JSONB)
    corrected_value = Column(JSONB, nullable=False)

    notes = Column(Text)
    corrected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
