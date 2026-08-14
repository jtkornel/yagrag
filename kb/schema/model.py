"""Schema model for the knowledge base graph.

The schema is expressed as plain Python/Pydantic objects and serialized to
JSON on disk. Kuzu DDL is generated from the model, so the same declaration
is used for `apply`, `validate`, and `show`.

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


# --- Kuzu type whitelist -----------------------------------------------------

_SCALAR_TYPES = {
    "STRING",
    "INT64",
    "INT32",
    "INT16",
    "INT8",
    "DOUBLE",
    "FLOAT",
    "BOOL",
    "DATE",
    "TIMESTAMP",
    "UUID",
    "SERIAL",
}


def _is_valid_type(t: str) -> bool:
    t = t.strip()
    if t in _SCALAR_TYPES:
        return True
    # LIST types: e.g. STRING[], INT64[]
    if t.endswith("[]") and t[:-2] in _SCALAR_TYPES:
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
            raise ValueError(f"unsupported Kuzu property type: {v!r}")
        return v


# Common properties auto-injected into every node type (unless opt-out).
# `id` is the primary key; other fields provide identity + provenance.
NODE_COMMON_PROPERTIES: tuple[Property, ...] = (
    Property(name="id", type="STRING", primary_key=True),
    Property(name="name", type="STRING"),
    Property(name="summary", type="STRING"),
    Property(name="origin", type="STRING"),
    Property(name="sources", type="STRING[]"),
    Property(name="confidence", type="DOUBLE"),
    Property(name="created_at", type="TIMESTAMP"),
    Property(name="updated_at", type="TIMESTAMP"),
)

# Provenance properties for relations (no primary key; Kuzu manages rel ids).
REL_COMMON_PROPERTIES: tuple[Property, ...] = (
    Property(name="origin", type="STRING"),
    Property(name="sources", type="STRING[]"),
    Property(name="confidence", type="DOUBLE"),
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
    pairs: list[RelPair]
    properties: list[Property] = Field(default_factory=list)
    include_common: bool = True

    @field_validator("pairs")
    @classmethod
    def _at_least_one_pair(cls, v: list[RelPair]) -> list[RelPair]:
        if not v:
            raise ValueError("relation type must declare at least one (from, to) pair")
        return v

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


# --- DDL rendering -----------------------------------------------------------


def render_property(p: Property) -> str:
    return f"{p.name} {p.type}"


def render_create_node_table(nt: NodeType) -> str:
    props = nt.effective_properties()
    pk = next(p for p in props if p.primary_key)
    cols = ", ".join(render_property(p) for p in props)
    return f"CREATE NODE TABLE {nt.name}({cols}, PRIMARY KEY({pk.name}))"


def render_create_rel_table(rt: RelationType) -> str:
    props = rt.effective_properties()
    pair_parts = [f"FROM {p.from_} TO {p.to}" for p in rt.pairs]
    parts = pair_parts + [render_property(p) for p in props]
    return f"CREATE REL TABLE {rt.name}({', '.join(parts)})"


# --- Full schema container ---------------------------------------------------


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
                    rt.pairs.append(RelPair(from_=raw.from_, to=raw.to))
            elif isinstance(raw, CypherOp):
                pass  # opaque
