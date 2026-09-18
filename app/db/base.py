from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Model imports live here so Alembic's autogenerate sees every table via
# Base.metadata. They sit below Base because the model modules import it, and
# they are plain `import x.y` statements rather than `from x import Y`: the
# latter needs the target module fully initialised, which deadlocks whenever
# app.models is the import entry point instead of app.db.base.
import app.models.order  # noqa: E402,F401
import app.models.order_item  # noqa: E402,F401
import app.models.restaurant  # noqa: E402,F401
import app.models.restaurant_item  # noqa: E402,F401
import app.models.user  # noqa: E402,F401
