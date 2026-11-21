import os
from typing import get_args

import yaml
from pydantic import BaseModel, Field, model_validator

from app_models import DisputeReason


class SlotConfig(BaseModel):
    id: str
    question: str


class CommonSlots(BaseModel):
    before: list[SlotConfig] = Field(default_factory=list)
    after: list[SlotConfig] = Field(default_factory=list)


class DisputeTypeConfig(BaseModel):
    label: str
    visa_code: str = ""
    menu_text: str
    description: str = ""
    uses_common_slots: bool = True
    slots: list[SlotConfig] = Field(default_factory=list)


class DisputeRegistry(BaseModel):
    """The per-reason question sets from prompts/dispute_types.yml."""
    common_slots: CommonSlots = Field(default_factory=CommonSlots)
    types: dict[str, DisputeTypeConfig]

    @model_validator(mode="after")
    def _validate(self) -> "DisputeRegistry":
        known = set(get_args(DisputeReason))
        unknown = set(self.types) - known
        if unknown:
            raise ValueError(f"Unknown dispute reason id(s) in dispute_types.yml: {sorted(unknown)}")
        missing = known - set(self.types)
        if missing:
            raise ValueError(f"dispute_types.yml has no entry for: {sorted(missing)}")
        for reason in self.types:
            ids = [s.id for s in self.slots_for(reason)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate slot id for reason '{reason}': {ids}")
            if any(not s.question.strip() for s in self.slots_for(reason)):
                raise ValueError(f"Empty question for reason '{reason}'")
        return self

    def slots_for(self, reason: str) -> list[SlotConfig]:
        """Slots to collect for a reason, in the order they are asked."""
        t = self.types[reason]
        if not t.uses_common_slots:
            return list(t.slots)
        return [*self.common_slots.before, *t.slots, *self.common_slots.after]

    def menu(self) -> list[tuple[str, str]]:
        """(reason id, customer-facing wording) for every reason, in file order."""
        return [(rid, t.menu_text) for rid, t in self.types.items()]


def load_dispute_registry(filename: str) -> DisputeRegistry:
    """Load and validate the registry. `filename` lives in <project root>/prompts/."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    path = os.path.join(base_dir, "prompts", filename)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return DisputeRegistry.model_validate(data)
