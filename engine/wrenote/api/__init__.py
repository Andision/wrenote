"""HTTP API routers.

Each module exposes a ``router`` (a :class:`fastapi.APIRouter`) for one
resource family. ``wrenote.server`` (later ``wrenote.app.create_app``) includes
them. Routers depend only on ``api._common`` and ``core/*`` — never on
``server`` — so there is no import cycle.
"""
