"""remove Event Model

Revision ID: 7a79d6a6f019
Revises: 28c96417c73c
Create Date: 2025-07-14 16:07:06.153170

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '7a79d6a6f019'
down_revision = '28c96417c73c'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('event')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('event',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('contact_id', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('title', sa.VARCHAR(length=200), autoincrement=False, nullable=False),
    sa.Column('date', postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('location', sa.VARCHAR(length=200), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['contact_id'], ['contact.id'], name=op.f('event_contact_id_fkey')),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('event_user_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('event_pkey'))
    )
    # ### end Alembic commands ###
