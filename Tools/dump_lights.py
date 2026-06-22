import json
import os

import unreal


MAP_PATH = "/Game/ModularSciFi/Levels/AAA_nb"
OUT_PATH = os.path.join(unreal.Paths.project_saved_dir(), "lighting_audit_AAA_nb.json")


def vec_to_list(v):
    return [round(float(v.x), 3), round(float(v.y), 3), round(float(v.z), 3)]


def color_to_list(c):
    return [round(float(c.r), 3), round(float(c.g), 3), round(float(c.b), 3), round(float(c.a), 3)]


def light_component(actor):
    comps = actor.get_components_by_class(unreal.LightComponentBase)
    return comps[0] if comps else None


unreal.EditorLoadingAndSavingUtils.load_map(MAP_PATH)

try:
    wp = unreal.get_editor_subsystem(unreal.WorldPartitionEditorSubsystem)
    if wp:
        try:
            wp.load_all_cells()
        except Exception as exc:
            print("WORLD_PARTITION_LOAD_ALL_CELLS_FAILED", exc)
except Exception as exc:
    print("WORLD_PARTITION_SUBSYSTEM_UNAVAILABLE", exc)

actors = unreal.EditorLevelLibrary.get_all_level_actors()
lights = []

for actor in actors:
    comp = light_component(actor)
    if not comp:
        continue

    entry = {
        "actor": actor.get_actor_label(),
        "class": actor.get_class().get_name(),
        "location": vec_to_list(actor.get_actor_location()),
        "mobility": str(comp.get_editor_property("mobility")),
        "visible": bool(comp.get_editor_property("visible")),
        "cast_shadows": bool(comp.get_editor_property("cast_shadows")),
        "affects_world": bool(comp.get_editor_property("affects_world")),
        "intensity": float(comp.get_editor_property("intensity")),
        "light_color": color_to_list(comp.get_editor_property("light_color")),
    }

    for prop in (
        "attenuation_radius",
        "source_radius",
        "soft_source_radius",
        "source_length",
        "inner_cone_angle",
        "outer_cone_angle",
        "volumetric_scattering_intensity",
        "indirect_lighting_intensity",
        "max_draw_distance",
        "max_distance_fade_range",
    ):
        if hasattr(comp, "get_editor_property"):
            try:
                entry[prop] = float(comp.get_editor_property(prop))
            except Exception:
                pass

    lights.append(entry)

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump({"map": MAP_PATH, "light_count": len(lights), "lights": lights}, f, indent=2)

print("LIGHTING_AUDIT_WRITTEN", OUT_PATH)
