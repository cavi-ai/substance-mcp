"""Engine export profiles shared by Sampler tools and batch jobs."""

from dataclasses import dataclass

TARGET_NAMES = ("unreal", "unity", "blender", "generic")
OUTPUT_KINDS = ("sbsar", "textures")
TEXTURE_FORMATS = ("png", "tga", "tif", "exr")


@dataclass(frozen=True)
class Target:
    name: str
    preset: str | None
    maps: tuple[str, ...]
    description: str


TARGETS = {
    "unreal": Target(
        "unreal", "Unreal Engine 4",
        ("basecolor", "normal", "occlusionroughnessmetallic"),
        "Unreal basecolor, normal and packed ORM maps.",
    ),
    "unity": Target(
        "unity", "Unity HD Render Pipeline Standard",
        ("basecolor", "normal", "maskmap"),
        "Unity HDRP basecolor, normal and mask map.",
    ),
    "blender": Target(
        "blender", "Blender Cycles/Eevee",
        ("basecolor", "normal", "roughness", "metallic"),
        "Blender basecolor, normal, roughness and metallic maps.",
    ),
    "generic": Target(
        "generic", None, (),
        "Raw textures using caller-selected channels, plus .sbsar.",
    ),
}


def target(name: str) -> Target:
    try:
        return TARGETS[name]
    except KeyError:
        raise ValueError(
            f"target must be one of: {', '.join(TARGET_NAMES)}"
        ) from None


def preset_for(name: str, override: str | None = None) -> str | None:
    if override is not None:
        return override
    return target(name).preset


def catalog() -> dict[str, dict]:
    return {
        name: {
            "preset": item.preset,
            "maps": list(item.maps),
            "outputs": list(OUTPUT_KINDS),
            "description": item.description,
        }
        for name, item in TARGETS.items()
    }
