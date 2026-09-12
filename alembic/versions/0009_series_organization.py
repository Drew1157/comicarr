#  Copyright (C) 2026 Comicarr contributors
#
#  This file is part of Comicarr.
#
#  Comicarr is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

"""Add operator-controlled franchise and main-hero organization fields.

Revision ID: 0009_series_organization
Revises: 0008_manga_series_modes
"""

import sqlalchemy as sa

from alembic import op

revision = "0009_series_organization"
down_revision = "0008_manga_series_modes"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("comics")}
    for name in ("Franchise", "MainHero"):
        if name not in columns:
            op.add_column("comics", sa.Column(name, sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("comics")}
    for name in ("MainHero", "Franchise"):
        if name in columns:
            op.drop_column("comics", name)
