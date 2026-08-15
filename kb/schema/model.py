"""Schema model for the knowledge base graph.

The schema is expressed as Python/Pydantic objects and serialized as native
ISO GQL / openCypher DDL (`.gql`).

Provenance is enforced structurally: every node and relation type has a set
of common properties auto-injected (`origin`, `sources`, `confidence`,
`created_at`, `updated_at`) unless a type explicitly opts out. Nodes get
additional identity fields (`id` primary key, `name`, `summary`) unless
already declared.

The reified `Claim` pattern is *not* a special node type in code — it is
just a `NodeType` with subject/predicate/object semantic properties plus
`ABOUT`/`HAS_OBJECT` relations, declared like any other domain type.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --- Property types ----------------------------------------------------------

_SCALAR_TYPES = {
    "STRING",
    "INT64",
    "INT32",
    "INT16",
    "INT8",
    "DOUBLE",
    "FLOAT",
    "FLOAT64",
    "FLOAT32",
    "BOOL",
    "BOOLEAN",
    "DATE",
    "TIMESTAMP",
    "UUID",
    "SERIAL",
    "LIST",
    "ANY",
}


def _is_valid_type(t: str) -> bool:
    t = t.strip().upper()
    if t in _SCALAR_TYPES:
        return True
    if t.startswith("LIST<") and t.endswith(">"):
        return _is_valid_type(t[5:-1])
    if t.endswith("[]") and _is_valid_type(t[:-2]):
        return True
    return False


# --- Property / NodeType / RelationType --------------------------------------


class Property(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    primary_key: bool = False

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if not _is_valid_type(v):
            raise ValueError(f"unsupported property type: {v!r}")
        return v


# Common properties auto-injected into every node type (unless opt-out).
# `id` is the primary key; other fields provide identity + provenance.
NODE_COMMON_PROPERTIES: tuple[Property, ...] = (
    Property(name="id", type="STRING", primary_key=True),
    Property(name="name", type="STRING"),
    Property(name="summary", type="STRING"),
    Property(name="origin", type="STRING"),
    Property(name="sources", type="LIST"),
    Property(name="confidence", type="FLOAT64"),
    Property(name="created_at", type="TIMESTAMP"),
    Property(name="updated_at", type="TIMESTAMP"),
)

# Provenance properties for relations.
REL_COMMON_PROPERTIES: tuple[Property, ...] = (
    Property(name="origin", type="STRING"),
    Property(name="sources", type="LIST"),
    Property(name="confidence", type="FLOAT64"),
    Property(name="created_at", type="TIMESTAMP"),
    Property(name="updated_at", type="TIMESTAMP"),
)


class NodeType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    properties: list[Property] = Field(default_factory=list)
    include_common: bool = True

    def effective_properties(self) -> list[Property]:
        """Return the full property list including auto-injected common ones.

        User-declared properties win by name; the common set fills in the rest.
        """
        by_name: dict[str, Property] = {p.name: p for p in self.properties}
        if self.include_common:
            for p in NODE_COMMON_PROPERTIES:
                by_name.setdefault(p.name, p)
        # Preserve a stable order: common first (in canonical order), then
        # any user-declared extras in declaration order.
        ordered: list[Property] = []
        seen: set[str] = set()
        if self.include_common:
            for p in NODE_COMMON_PROPERTIES:
                ordered.append(by_name[p.name])
                seen.add(p.name)
        for p in self.properties:
            if p.name not in seen:
                ordered.append(by_name[p.name])
                seen.add(p.name)
        # Ensure exactly one primary key.
        pk = [p for p in ordered if p.primary_key]
        if len(pk) != 1:
            raise ValueError(
                f"node type {self.name!r} must have exactly one primary key, "
                f"found {len(pk)}"
            )
        return ordered


class RelPair(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class RelationType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    pairs: list[RelPair] = Field(default_factory=list)
    properties: list[Property] = Field(default_factory=list)
    include_common: bool = True

    def effective_properties(self) -> list[Property]:
        by_name: dict[str, Property] = {p.name: p for p in self.properties}
        if self.include_common:
            for p in REL_COMMON_PROPERTIES:
                by_name.setdefault(p.name, p)
        ordered: list[Property] = []
        seen: set[str] = set()
        if self.include_common:
            for p in REL_COMMON_PROPERTIES:
                ordered.append(by_name[p.name])
                seen.add(p.name)
        for p in self.properties:
            if p.name not in seen:
                ordered.append(by_name[p.name])
                seen.add(p.name)
        # Relations must not carry a primary key.
        if any(p.primary_key for p in ordered):
            raise ValueError(
                f"relation type {self.name!r}: primary_key not allowed on relation properties"
            )
        return ordered


# --- Grafeo / GQL DDL rendering ----------------------------------------------


def grafeo_property_type(t: str) -> str:
    """Map internal scalar/list types to Grafeo property types."""
    t = t.strip()
    if t.endswith("[]"):
        return "LIST"
    type_map = {
        "DOUBLE": "FLOAT64",
        "FLOAT": "FLOAT32",
        "BOOL": "BOOLEAN",
    }
    return type_map.get(t.upper(), t)


def render_create_node_type_grafeo(nt: NodeType) -> str:
    """Render a Grafeo `CREATE NODE TYPE` statement."""
    props = nt.effective_properties()
    cols = ", ".join(f"{p.name} {grafeo_property_type(p.type)}" for p in props)
    return f"CREATE NODE TYPE {nt.name} ({cols})"


def render_create_edge_type_grafeo(rt: RelationType) -> str:
    """Render a Grafeo `CREATE EDGE TYPE` statement."""
    props = rt.effective_properties()
    cols = ", ".join(f"{p.name} {grafeo_property_type(p.type)}" for p in props)
    return f"CREATE EDGE TYPE {rt.name} ({cols})"


# Backward-compatibility aliases
render_create_node_table = render_create_node_type_grafeo
render_create_rel_table = render_create_edge_type_grafeo


# --- GQL (ISO/IEC 39075) DDL rendering ---------------------------------------


def _gql_type(t: str) -> str:
    """Map internal scalar/list types to standard ISO GQL data types."""
    t = t.strip()
    if t == "LIST":
        return "LIST<STRING>"
    if t.endswith("[]"):
        inner = _gql_type(t[:-2])
        return f"LIST<{inner}>"
    type_map = {
        "STRING": "STRING",
        "INT64": "INT64",
        "INT32": "INT32",
        "INT16": "INT16",
        "INT8": "INT8",
        "DOUBLE": "FLOAT64",
        "FLOAT": "FLOAT32",
        "FLOAT64": "FLOAT64",
        "FLOAT32": "FLOAT32",
        "BOOL": "BOOLEAN",
        "BOOLEAN": "BOOLEAN",
        "DATE": "DATE",
        "TIMESTAMP": "TIMESTAMP",
        "UUID": "UUID",
        "SERIAL": "INT64",
        "ANY": "ANY",
    }
    return type_map.get(t.upper(), t)


def render_gql_property(p: Property) -> str:
    """Render a single property declaration in ISO GQL syntax."""
    gql_t = _gql_type(p.type)
    nullability = " NOT NULL" if p.primary_key else ""
    return f"{p.name} {gql_t}{nullability}"


def render_create_node_type_gql(nt: NodeType) -> str:
    """Render a standard ISO GQL `NODE TYPE` definition."""
    props = nt.effective_properties()
    cols = ",\n        ".join(render_gql_property(p) for p in props)
    return f"    NODE TYPE {nt.name} {{\n        {cols}\n    }}"


def render_create_edge_type_gql(rt: RelationType) -> str:
    """Render a standard ISO GQL `EDGE TYPE` definition with endpoint pairs."""
    props = rt.effective_properties()
    props_decl = ",\n        ".join(render_gql_property(p) for p in props)
    if rt.pairs:
        pair_parts = " |\n        ".join(f"{p.from_} TO {p.to}" for p in rt.pairs)
        return (
            f"    EDGE TYPE {rt.name} CONNECTING (\n"
            f"        {pair_parts}\n"
            f"    ) {{\n"
            f"        {props_decl}\n"
            f"    }}"
        )
    return f"    EDGE TYPE {rt.name} {{\n        {props_decl}\n    }}"


def render_gql_graph_type(
    schema: Schema, graph_type_name: str = "KnowledgeBaseGraphType"
) -> str:
    """Render the full declarative schema as an ISO GQL (ISO/IEC 39075) GRAPH TYPE."""
    nodes = ",\n\n".join(render_create_node_type_gql(nt) for nt in schema.node_types)
    edges = ",\n\n".join(render_create_edge_type_gql(rt) for rt in schema.relation_types)
    return (
        f"CREATE GRAPH TYPE {graph_type_name} AS {{\n"
        f"{nodes},\n\n"
        f"{edges}\n"
        f"}}"
    )


class Schema(BaseModel):
    """A declarative schema: node types + relation types.

    A schema is the *target* description of the DB. Migrations translate a
    stream of individual operations into DDL; a `Schema` object is what the
    accumulated migrations describe. It is also what `kb schema show` prints.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    node_types: list[NodeType] = Field(default_factory=list)
    relation_types: list[RelationType] = Field(default_factory=list)

    def node_type_names(self) -> list[str]:
        return [nt.name for nt in self.node_types]

    def relation_type_names(self) -> list[str]:
        return [rt.name for rt in self.relation_types]


# --- Migration operations ----------------------------------------------------


class CreateNodeOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["create_node_table"]
    table: NodeType


class CreateRelOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["create_rel_table"]
    table: RelationType


class AddRelPairOp(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    op: Literal["add_rel_pair"]
    table: str
    from_: str = Field(alias="from")
    to: str


class CypherOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["cypher"]
    sql: str


MigrationOp = Annotated[
    Union[CreateNodeOp, CreateRelOp, AddRelPairOp, CypherOp],
    Field(discriminator="op"),
]


class Migration(BaseModel):
    """A single migration file, identified by its `id` (usually `NNNN_name`)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = ""
    operations: list[MigrationOp] = Field(default_factory=list)

    def apply_to_schema(self, schema: Schema) -> None:
        """Fold this migration's structural operations into `schema` in place.

        `cypher` escape-hatch ops are opaque to the schema model and skipped.
        """
        for raw in self.operations:
            if isinstance(raw, CreateNodeOp):
                if raw.table.name in schema.node_type_names():
                    raise ValueError(f"duplicate node type: {raw.table.name}")
                schema.node_types.append(raw.table)
            elif isinstance(raw, CreateRelOp):
                if raw.table.name in schema.relation_type_names():
                    raise ValueError(f"duplicate relation type: {raw.table.name}")
                schema.relation_types.append(raw.table)
            elif isinstance(raw, AddRelPairOp):
                rt = next((r for r in schema.relation_types if r.name == raw.table), None)
                if rt is None:
                    raise ValueError(f"unknown relation type for add_rel_pair: {raw.table}")
                if not any(p.from_ == raw.from_ and p.to == raw.to for p in rt.pairs):
                    rt.pairs.append(RelPair(**{"from": raw.from_, "to": raw.to}))
            elif isinstance(raw, CypherOp):
                pass  # opaque
