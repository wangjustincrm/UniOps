"""Features (≈ ABP's Feature management, user 2026-11-23): a switch is defined once (FeatureDefinition) and its value
comes from a chain of providers (Chain of Responsibility, as ABP's value providers): the server's environment, then a
stored value (an audited setting: who, when, why).

One deviation from ABP, for safety: a definition with `env_is_ceiling` (the kill-switches: partner writes, external
email, the scheduler) is ON only when the environment says on AND the stored value does not say off — a stored value
can turn it off, never on past what the server allows. A deploy that keeps a switch off stays off whatever the
database says.

The stored values are read into an in-process cache (refreshed on every change and by a tick), so a guard that runs
outside a database session reads them synchronously. `FeatureSettingsView` (Proxy) answers a feature's name with its
effective value and anything else from the settings it wraps, so code that read `settings.<SWITCH>` keeps working.
"""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureDefinition:
    name: str              # the environment variable / settings attribute, e.g. "<MODULE>_PARTNER_WRITES_ENABLED"
    display_name: str
    description: str = ""
    default: bool = False
    env_is_ceiling: bool = True


class EnvironmentValueProvider:
    """The server's own value (its environment, through the settings object)."""
    def get(self, d: FeatureDefinition, settings: Any) -> bool:
        return getattr(settings, d.name, d.default) is True


class StoredValueProvider:
    """The value a person stored (None: none stored)."""
    def __init__(self) -> None:
        self.values: dict[str, bool | None] = {}

    def get(self, d: FeatureDefinition) -> bool | None:
        return self.values.get(d.name)


class FeatureManager:
    def __init__(self, definitions: list[FeatureDefinition], settings: Any = None) -> None:
        self.definitions = {d.name: d for d in definitions}
        self.settings = settings
        self.env = EnvironmentValueProvider()
        self.stored = StoredValueProvider()

    def effective(self, name: str, settings: Any = None) -> bool:
        d = self.definitions[name]
        env, stored = self.env.get(d, settings if settings is not None else self.settings), self.stored.get(d)
        if d.env_is_ceiling:
            return env and stored is not False
        return env if stored is None else stored

    def note_stored(self, name: str, value: bool | None) -> None:
        self.stored.values[name] = value

    def state(self, settings: Any = None) -> list[dict[str, Any]]:
        s = settings if settings is not None else self.settings
        return [{"name": d.name, "display_name": d.display_name, "description": d.description,
                 "env": self.env.get(d, s), "stored": self.stored.get(d), "effective": self.effective(d.name, s),
                 "env_is_ceiling": d.env_is_ceiling} for d in self.definitions.values()]


class FeatureSettingsView:
    """The settings, with each feature's name answered by its effective value (the Proxy pattern)."""

    def __init__(self, settings: Any, features: FeatureManager) -> None:
        object.__setattr__(self, "_settings", settings)
        object.__setattr__(self, "_features", features)

    def __getattr__(self, name: str) -> Any:
        features = object.__getattribute__(self, "_features")
        settings = object.__getattribute__(self, "_settings")
        if name in features.definitions:
            return features.effective(name, settings)
        return getattr(settings, name)
