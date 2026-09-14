"""MicroserviceGenerator — produces production-ready FastAPI microservice from a MicroserviceSpec.

Returns {relative_path: file_content} for every file in the generated service.

Split into per-concern mixins (each < 500 lines) to keep files under the
architecture validator's size limits: WebLayerMixin (main/schemas/deps/
exceptions/routes), PersistenceSqlMixin + PersistenceMongoMixin (database/
models/crud/alembic), InfraMixin (Dockerfile/compose/pyproject/.env). The
public contract is unchanged — `MicroserviceGenerator(spec).generate()` and
the module-level `generate()` function behave exactly as before.
"""

from __future__ import annotations

from .spec import MicroserviceSpec
from ._web_mixin import WebLayerMixin
from ._persistence_sql_mixin import PersistenceSqlMixin
from ._persistence_mongo_mixin import PersistenceMongoMixin
from ._infra_mixin import InfraMixin


class MicroserviceGenerator(
    WebLayerMixin,
    PersistenceSqlMixin,
    PersistenceMongoMixin,
    InfraMixin,
):
    def __init__(self, spec: MicroserviceSpec) -> None:
        self.spec = spec
        self.s = spec
        self.cls = spec.class_name
        self.tbl = spec.table_name
        self.n = spec.name

    def generate(self) -> dict[str, str]:
        files: dict[str, str] = {}
        files[f"{self.n}/__init__.py"] = ""
        files[f"{self.n}/main.py"] = self._main()
        files[f"{self.n}/schemas.py"] = self._schemas()
        files[f"{self.n}/dependencies.py"] = self._dependencies()
        files[f"{self.n}/exceptions.py"] = self._exceptions()
        files[f"{self.n}/routes.py"] = self._routes()
        files[f"{self.n}/.env.example"] = self._env_example()
        files[f"{self.n}/Dockerfile"] = self._dockerfile()
        files[f"{self.n}/docker-compose.yml"] = self._docker_compose()
        files[f"{self.n}/pyproject.toml"] = self._pyproject()

        if self.s.is_sql:
            files[f"{self.n}/database.py"] = self._database_sql()
            files[f"{self.n}/models.py"] = self._models_sql()
            files[f"{self.n}/crud.py"] = self._crud_sql()
            files[f"{self.n}/alembic.ini"] = self._alembic_ini()
            files[f"{self.n}/alembic/__init__.py"] = ""
            files[f"{self.n}/alembic/env.py"] = self._alembic_env()
            files[f"{self.n}/alembic/versions/.gitkeep"] = ""
        else:
            files[f"{self.n}/database.py"] = self._database_mongo()
            files[f"{self.n}/models.py"] = self._models_mongo()
            files[f"{self.n}/crud.py"] = self._crud_mongo()

        return files


def generate(spec: MicroserviceSpec) -> dict[str, str]:
    """Entry point — returns {relative_path: content} for every generated file."""
    return MicroserviceGenerator(spec).generate()
