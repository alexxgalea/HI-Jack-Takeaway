from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Model imports go here so Alembic's autogenerate can see them via Base.metadata.
# Added starting in M1.
