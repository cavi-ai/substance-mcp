"""Typed, declarative Designer graph authoring requests."""

from typing import Annotated, Literal, TypeAliasType

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt

Identifier = TypeAliasType(
    "Identifier", Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")]
)
NodeId = TypeAliasType(
    "NodeId", Annotated[str, Field(pattern=r"^[A-Za-z0-9_:-]{1,128}$")]
)
Number = StrictInt | Annotated[StrictFloat, Field(allow_inf_nan=False)]
Value = TypeAliasType("Value", StrictBool | Number | str | list[Number])
Position = TypeAliasType("Position", tuple[Number, Number])


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AddNode(Edit):
    action: Literal['add_node']
    key: NodeId
    definition: Annotated[str, Field(min_length=1, max_length=256)]
    values: dict[str, Value] = Field(default_factory=dict, max_length=128)
    position: Position = (0, 0)


class Instance(Edit):
    action: Literal['instance']
    key: NodeId
    package: str
    resource: Annotated[str, Field(pattern=r'^pkg://', min_length=7, max_length=512)]
    values: dict[str, Value] = Field(default_factory=dict, max_length=128)
    position: Position = (0, 0)


class SetValues(Edit):
    action: Literal['set']
    node: NodeId
    values: dict[str, Value] = Field(min_length=1, max_length=128)


class Connect(Edit):
    action: Literal['connect']
    source: NodeId
    source_port: str
    target: NodeId
    target_port: str


class Disconnect(Edit):
    action: Literal['disconnect']
    node: NodeId
    property: str


class Remove(Edit):
    action: Literal['remove_node']
    node: NodeId


class Expose(Edit):
    action: Literal['expose']
    node: NodeId
    property: str
    identifier: Identifier
    label: str | None = None
    minimum: Number | None = None
    maximum: Number | None = None


class SetInput(Edit):
    action: Literal['set_input']
    identifier: Identifier
    value: Value


class Output(Edit):
    action: Literal['output']
    key: NodeId
    source: NodeId
    source_port: str
    identifier: Identifier
    usage: Identifier
    components: Literal['R', 'G', 'B', 'A', 'RGB', 'RGBA'] = 'RGBA'
    color_space: str = ''
    position: Position = (400, 0)


GraphEdit = Annotated[AddNode | Instance | SetValues | Connect | Disconnect | Remove
                      | Expose | SetInput | Output, Field(discriminator='action')]


class GraphRecipe(Edit):
    identifier: Identifier = 'material'
    resolution: Literal[32, 64, 128, 256, 512, 1024, 2048, 4096, 8192] = 2048
    precision: Literal['8', '16', '16f', '32f'] = '16f'
    seed: Annotated[StrictInt, Field(ge=0, le=2147483647)] = 0
    operations: list[GraphEdit] = Field(default_factory=list, max_length=512)
