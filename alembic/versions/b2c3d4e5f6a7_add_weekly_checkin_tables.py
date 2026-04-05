"""add weekly checkin tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create weekly_screen_time_logs table
    op.create_table('weekly_screen_time_logs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('group_id', sa.Uuid(), nullable=False),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('app_name', sa.String(length=255), nullable=False),
        sa.Column('minutes_used', sa.Integer(), nullable=False),
        sa.Column('screenshot_url', sa.String(length=2048), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'group_id', 'week_start', 'app_name', name='uq_weekly_stl_user_group_week_app'),
    )

    # Create weekly_checkins table
    op.create_table('weekly_checkins',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('weekly_total_minutes', sa.Integer(), nullable=False),
        sa.Column('daily_sum_minutes', sa.Integer(), nullable=False),
        sa.Column('discrepancy_minutes', sa.Integer(), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('ocr_source', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'week_start', name='uq_weekly_checkin_user_week'),
    )


def downgrade() -> None:
    op.drop_table('weekly_checkins')
    op.drop_table('weekly_screen_time_logs')
