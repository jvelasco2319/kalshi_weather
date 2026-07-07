from __future__ import annotations

from dataclasses import dataclass

from kalshi_weather.model.version import MODEL_VERSION


@dataclass(frozen=True)
class ModelSpec:
    version: str
    description: str
    demo_only: bool = False


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "v0.3-openmeteo-weighted-normal-residual": ModelSpec(
        "v0.3-openmeteo-weighted-normal-residual",
        "Weighted Open-Meteo future high with normal residual uncertainty.",
    ),
    "v0.4-calibrated-residual-sigma": ModelSpec(
        "v0.4-calibrated-residual-sigma",
        "Weighted Open-Meteo model with residual sigma optionally derived from joined rows.",
    ),
    "demo-fixture-model": ModelSpec(
        "demo-fixture-model",
        "Offline demonstration model. Not trading evidence.",
        demo_only=True,
    ),
}


def list_model_versions() -> list[str]:
    return list(MODEL_REGISTRY)


def get_model_spec(version: str | None = None) -> ModelSpec:
    selected = version or MODEL_VERSION
    try:
        return MODEL_REGISTRY[selected]
    except KeyError as exc:
        known = ", ".join(list_model_versions())
        raise ValueError(f"Unknown model version {selected!r}. Known versions: {known}") from exc
