from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class DatabricksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str = Field(
        default="",
        description="Databricks workspace URL, for example https://<workspace>.azuredatabricks.net.",
    )
    token: str = Field(
        default="",
        description="Databricks personal access token or service principal token.",
    )
    cluster_id: str = Field(default="", description="Existing Databricks cluster ID.")
    catalog: str = "hive_metastore"
    schema_name: str = Field(
        default="default",
        validation_alias=AliasChoices("schema", "schema_name"),
        serialization_alias="schema",
    )


class IngestRequest(BaseModel):
    table_name: str
    mode: Literal["overwrite", "append", "error", "errorifexists", "ignore"] = (
        "overwrite"
    )
    databricks_config: DatabricksConfig


class QueryRequest(BaseModel):
    sql: str
    databricks_config: DatabricksConfig
