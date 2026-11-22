"""Engine targets for Painter texture export, expanded before Painter runs."""

TARGET_NAMES = ("unreal", "unity", "blender")

# Channel sources are the strings map_source already accepts. Format and bit depth
# stay on the export call so file_format and bit_depth still apply.
_MAPS = {
    "unreal": (
        ("T_$textureSet_BaseColor", (("RGB", "basecolor"),)),
        ("T_$textureSet_Normal", (("RGB", "normal_directx"),)),
        ("T_$textureSet_OcclusionRoughnessMetallic", (
            ("R", "ambientocclusion"), ("G", "roughness"), ("B", "metallic"),
        )),
    ),
    "unity": (
        ("T_$textureSet_BaseColor", (("RGB", "basecolor"),)),
        ("T_$textureSet_Normal", (("RGB", "normal_opengl"),)),
        ("T_$textureSet_MaskMap", (
            ("R", "metallic"), ("G", "ambientocclusion"), ("B", "0"), ("A", "1-roughness"),
        )),
    ),
    "blender": (
        ("T_$textureSet_BaseColor", (("RGB", "basecolor"),)),
        ("T_$textureSet_Normal", (("RGB", "normal_opengl"),)),
        ("T_$textureSet_Roughness", (("L", "roughness"),)),
        ("T_$textureSet_Metallic", (("L", "metallic"),)),
    ),
}


def expand(target, preset, maps):
    """Return the preset and maps Painter receives.

    A target becomes channel maps here. Painter still receives exactly one of preset
    or maps, and never the target name.
    """
    if target is not None and preset is not None:
        raise ValueError("target cannot be combined with preset")
    if target is not None and maps is not None:
        raise ValueError("target cannot be combined with maps")
    if target is None:
        return preset, maps
    try:
        chosen = _MAPS[target]
    except KeyError:
        raise ValueError(f"target must be one of: {', '.join(TARGET_NAMES)}") from None
    return None, [{"name": name, "channels": dict(channels)} for name, channels in chosen]
