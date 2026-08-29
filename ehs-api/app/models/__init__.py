"""Import every model module so Base.metadata is fully populated.

Alembic's env.py and the test suite's `Base.metadata.create_all` both rely on
this module having imported every model — one that is not reachable from here
is silently absent from migrations and from the test schema.
"""
from app.models.action import Action, ActionUpdate, ActionVerification  # noqa: F401
from app.models.communication import Communication  # noqa: F401
from app.models.config import EhsConfig, Holiday  # noqa: F401
from app.models.first_aid import FirstAidLog  # noqa: F401
from app.models.incident import (  # noqa: F401
    Incident, IncidentCause, IncidentInvestigation, IncidentPerson,
)
from app.models.mirrors import Department, Location, Task, User  # noqa: F401
from app.models.rtw import RtwCheckin, RtwPlan  # noqa: F401
from app.models.statutory import StatutoryDeadline  # noqa: F401
from app.models.training import Course, TrainingRecord, WorkerCertification  # noqa: F401
from app.models.vocabulary import Vocabulary, VocabularyItem  # noqa: F401
from app.models.worker import (  # noqa: F401
    JobPosition, PositionRequirement, WorkerPosition, WorkerProfile,
)
