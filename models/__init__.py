"""
Importing this package registers every model's table with database.Base's
metadata, regardless of which specific model a caller imports first --
several models now reference each other via string ForeignKey (e.g.
conversation_session.opened_by_staff_id -> kefu_staff.staff_id), which
SQLAlchemy can only resolve once both tables are registered. Without this,
whichever model happens to import first determines what's available, which
is fragile (see kefu-migration-plan.md's models -- request_log, session,
interaction_log, uchoice all reference kefu_staff/uchoice_customer).
"""
from . import group
from . import role
from . import service
from . import workflow
from . import uchoice
from . import kefu
from . import session
from . import request_log
from . import interaction_log
