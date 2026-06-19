import math
import unreal


TRIGGER_DISTANCE = 500.0
OPEN_ANGLE_DEGREES = 90.0
HINGE_SIDE = -1.0
OPEN_SECONDS = 1.0
REFRESH_SECONDS = 0.5
AUTO_DOOR_CLASS_TOKEN = "BP_AutoDoor"
DOOR_MESH_PATHS = {
    "door_1": "/Game/ModularSciFi/StaticMeshes/HardSurfaceEnvironment/Door1/SM_Door_1_Merged",
    "door_2": "/Game/ModularSciFi/StaticMeshes/HardSurfaceEnvironment/Door_2/SM_Door_2_Merged",
    "interior": "/Game/ModularSciFi/StaticMeshes/HardSurfaceEnvironment/Modular_Interior_Door/SM_Modular_Interior_Door_Merged",
}


class _AutoDoorState:
    def __init__(self, actor, mesh):
        self.actor = actor
        self.mesh = mesh
        self.key = make_actor_key(actor)
        location = get_relative_location(mesh)
        self.closed_location = unreal.Vector(location.x, location.y, location.z)
        rotation = get_relative_rotation(mesh)
        self.closed_rotation = unreal.Rotator(rotation.pitch, rotation.yaw, rotation.roll)
        self.hinge_offset = get_hinge_offset(mesh)
        self.open_direction = 1.0
        self.alpha = 0.0


class _AutoDoorSystem:
    def __init__(self):
        self._doors = []
        self._time_since_refresh = REFRESH_SECONDS
        self._tick_handle = None
        self._last_logged_count = -1

    def start(self):
        if self._tick_handle is None:
            self._tick_handle = unreal.register_slate_post_tick_callback(self.tick)
            unreal.log("AutoDoorSystem: started camera-distance door controller")

    def stop(self):
        if self._tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self._tick_handle)
            self._tick_handle = None

    def tick(self, delta_time):
        self._time_since_refresh += float(delta_time)
        if self._time_since_refresh >= REFRESH_SECONDS:
            self._time_since_refresh = 0.0
            self.refresh_doors()
            self.dedupe_doors()

        observer_locations = self.get_observer_locations()
        if not observer_locations:
            return

        trigger_distance_sq = TRIGGER_DISTANCE * TRIGGER_DISTANCE
        interp_speed = 1.0 / max(OPEN_SECONDS, 0.001)

        alive_doors = []
        moved_meshes = set()
        for door in self._doors:
            if not door.actor or not door.mesh:
                continue
            mesh_key = self.mesh_key(door.mesh)
            if mesh_key in moved_meshes:
                continue
            moved_meshes.add(mesh_key)

            observer_location, distance_sq = self.nearest_observer_for_door(observer_locations, door.actor, door.mesh)
            target_alpha = 1.0 if distance_sq < trigger_distance_sq else 0.0
            if target_alpha > 0.0 and observer_location is not None:
                door.open_direction = self.open_direction_away_from_observer(door.actor, observer_location)
            door.alpha = self.interp_constant_to(door.alpha, target_alpha, float(delta_time), interp_speed)

            angle = OPEN_ANGLE_DEGREES * door.alpha * door.open_direction
            new_location = rotated_location_about_hinge(door.closed_location, door.hinge_offset, angle)
            new_rotation = make_rotator(
                door.closed_rotation.pitch,
                door.closed_rotation.yaw + angle,
                door.closed_rotation.roll,
            )
            set_relative_location(door.mesh, new_location)
            set_relative_rotation(door.mesh, new_rotation)
            alive_doors.append(door)

        self._doors = alive_doors

    def refresh_doors(self):
        actors = self.get_level_actors()
        tracked = {door.key for door in self._doors if door.actor}

        for actor in actors:
            if not self.is_auto_door(actor):
                continue
            actor_key = make_actor_key(actor)
            if actor_key in tracked:
                continue

            mesh = self.find_door_mesh(actor)
            if mesh is None:
                unreal.log_warning(f"AutoDoorSystem: {actor.get_name()} has no StaticMeshComponent")
                continue
            actor_key = make_actor_key(actor)
            if actor_key in tracked:
                continue

            try:
                mesh.set_mobility(unreal.ComponentMobility.MOVABLE)
            except Exception:
                pass
            self.fix_instance_mesh(actor, mesh)

            self._doors.append(_AutoDoorState(actor, mesh))
            tracked.add(actor_key)

        if self._doors and len(self._doors) != self._last_logged_count:
            self._last_logged_count = len(self._doors)
            unreal.log(f"AutoDoorSystem: tracking {len(self._doors)} BP_AutoDoor actor(s)")

    def dedupe_doors(self):
        unique = []
        seen = set()
        for door in self._doors:
            key = door.key
            if key in seen:
                continue
            seen.add(key)
            unique.append(door)
        self._doors = unique

    def door_key(self, actor, mesh):
        if actor is None:
            return ""
        if mesh:
            mesh_path = self.mesh_key(mesh)
            if mesh_path:
                return mesh_path
        try:
            label = safe_actor_label(actor)
        except Exception:
            label = actor.get_name()
        mesh_name = ""
        if mesh:
            try:
                mesh_name = mesh.get_name()
            except Exception:
                mesh_name = ""
        return f"{label}|{mesh_name}"

    def mesh_key(self, mesh):
        if mesh is None:
            return ""
        try:
            return mesh.get_path_name()
        except Exception:
            try:
                return mesh.get_name()
            except Exception:
                return ""

    def get_level_actors(self):
        actors = []
        for world in self.get_candidate_worlds():
            try:
                actors.extend(unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Actor))
            except Exception:
                pass

        try:
            actors.extend(unreal.EditorLevelLibrary.get_all_level_actors())
        except Exception:
            pass

        unique_actors = []
        seen = set()
        for actor in actors:
            if not actor:
                continue
            path = actor.get_path_name()
            if path in seen:
                continue
            seen.add(path)
            unique_actors.append(actor)
        return unique_actors

    def get_observer_locations(self):
        locations = []
        for world in self.get_candidate_worlds():
            try:
                controller = unreal.GameplayStatics.get_player_controller(world, 0)
                if controller and controller.player_camera_manager:
                    locations.append(controller.player_camera_manager.get_camera_location())
                if controller:
                    pawn = controller.get_pawn()
                    if pawn:
                        locations.append(pawn.get_actor_location())
            except Exception:
                pass

        try:
            location, _rotation = unreal.EditorLevelLibrary.get_level_viewport_camera_info()
            if location is not None:
                locations.append(location)
        except Exception:
            pass

        unique_locations = []
        seen = set()
        for location in locations:
            if location is None:
                continue
            key = (round(location.x, 1), round(location.y, 1), round(location.z, 1))
            if key in seen:
                continue
            seen.add(key)
            unique_locations.append(location)
        return unique_locations

    def get_camera_location(self):
        locations = self.get_observer_locations()
        return locations[0] if locations else None

    def get_candidate_worlds(self):
        worlds = []
        try:
            worlds.extend(unreal.EditorLevelLibrary.get_pie_worlds())
        except Exception:
            pass
        try:
            editor_world = unreal.EditorLevelLibrary.get_editor_world()
            if editor_world:
                worlds.append(editor_world)
        except Exception:
            pass
        return worlds

    def is_auto_door(self, actor):
        if actor is None:
            return False
        actor_path = actor.get_path_name()
        if "/Engine/Transient" in actor_path or "TRASH_" in actor_path:
            return False
        actor_class = actor.get_class()
        class_name = actor_class.get_name() if actor_class else ""
        path_name = actor_class.get_path_name() if actor_class else ""
        return AUTO_DOOR_CLASS_TOKEN in class_name or "/ModularSciFi/Blueprints/BP_AutoDoor" in path_name

    def find_door_mesh(self, actor):
        try:
            components = actor.get_components_by_class(unreal.StaticMeshComponent)
        except Exception:
            components = []

        fallback = None
        for component in components:
            if not component:
                continue
            if not hasattr(component, "set_relative_location") and not hasattr(component, "set_editor_property"):
                continue

            static_mesh = None
            try:
                static_mesh = component.static_mesh
            except Exception:
                try:
                    static_mesh = component.get_editor_property("static_mesh")
                except Exception:
                    static_mesh = None

            if static_mesh is None:
                continue

            component_name = component.get_name()
            component_class_name = component.get_class().get_name()
            mesh_path = static_mesh.get_path_name()
            component_path = component.get_path_name()
            if "Gizmo" in component_class_name or "Gizmo" in component_path or "/Engine/VREditor/" in mesh_path:
                continue
            if "/Engine/Transient" in component_path:
                continue

            if fallback is None:
                fallback = component
            if component_name in ("StaticMeshComponent", "DoorMesh") or "/ModularSciFi/StaticMeshes/HardSurfaceEnvironment/" in mesh_path:
                return component

        return fallback

    def pick_mesh_for_door(self, door_actor):
        label = safe_actor_label(door_actor).lower()
        name = door_actor.get_name().lower()
        text = f"{label} {name}"
        if "modular_interior" in text or "interior" in text:
            key = "interior"
        elif "door_2" in text or "door 2" in text:
            key = "door_2"
        else:
            key = "door_1"

        mesh = unreal.EditorAssetLibrary.load_asset(DOOR_MESH_PATHS[key])
        if mesh is None:
            unreal.log_warning(f"AutoDoorSystem: mesh not found: {DOOR_MESH_PATHS[key]}")
        return mesh

    def fix_instance_mesh(self, door_actor, mesh_component):
        expected_mesh = self.pick_mesh_for_door(door_actor)
        if expected_mesh is None:
            return
        current_mesh = get_static_mesh(mesh_component)
        current_path = current_mesh.get_path_name() if current_mesh else ""
        expected_path = expected_mesh.get_path_name()
        if current_path == expected_path:
            return
        try:
            mesh_component.set_static_mesh(expected_mesh)
        except Exception:
            try:
                mesh_component.set_editor_property("static_mesh", expected_mesh)
            except Exception as exc:
                unreal.log_warning(f"AutoDoorSystem: could not fix mesh on {safe_actor_label(door_actor)}: {exc}")
                return
        unreal.log(f"AutoDoorSystem: fixed mesh on {safe_actor_label(door_actor)} -> {expected_path}")

    def nearest_door_distance_squared(self, camera_location, actor, mesh):
        points = [actor.get_actor_location(), get_component_world_location(mesh, actor)]
        bounds_origin = get_component_bounds_origin(mesh)
        if bounds_origin is not None:
            points.append(bounds_origin)
        return min(self.horizontal_distance_squared(camera_location, point) for point in points if point is not None)

    def nearest_observer_for_door(self, observer_locations, actor, mesh):
        best_location = None
        best_distance_sq = None
        for observer_location in observer_locations:
            distance_sq = self.nearest_door_distance_squared(observer_location, actor, mesh)
            if best_distance_sq is None or distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_location = observer_location
        if best_distance_sq is None:
            best_distance_sq = float("inf")
        return best_location, best_distance_sq

    def open_direction_away_from_observer(self, actor, observer_location):
        actor_location = actor.get_actor_location()
        actor_rotation = actor.get_actor_rotation()
        yaw_radians = math.radians(actor_rotation.yaw)
        dx = observer_location.x - actor_location.x
        dy = observer_location.y - actor_location.y
        local_y = -math.sin(yaw_radians) * dx + math.cos(yaw_radians) * dy
        return -1.0 if local_y >= 0.0 else 1.0

    @staticmethod
    def distance_squared(a, b):
        return (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2

    @staticmethod
    def horizontal_distance_squared(a, b):
        return (a.x - b.x) ** 2 + (a.y - b.y) ** 2

    @staticmethod
    def interp_constant_to(current, target, delta_time, speed):
        if math.isclose(current, target, abs_tol=1e-6):
            return target
        delta = target - current
        step = speed * delta_time
        if abs(delta) <= step:
            return target
        return current + math.copysign(step, delta)


def get_relative_location(component):
    if hasattr(component, "get_relative_location"):
        return component.get_relative_location()
    try:
        return component.get_editor_property("relative_location")
    except Exception:
        return component.relative_location


def get_relative_rotation(component):
    if hasattr(component, "get_relative_rotation"):
        return component.get_relative_rotation()
    try:
        return component.get_editor_property("relative_rotation")
    except Exception:
        try:
            return component.relative_rotation
        except Exception:
            return unreal.Rotator(0, 0, 0)


def make_rotator(pitch, yaw, roll):
    rotator = unreal.Rotator(0, 0, 0)
    try:
        rotator.pitch = pitch
        rotator.yaw = yaw
        rotator.roll = roll
        return rotator
    except Exception:
        pass
    try:
        rotator.set_editor_property("pitch", pitch)
        rotator.set_editor_property("yaw", yaw)
        rotator.set_editor_property("roll", roll)
    except Exception:
        return unreal.Rotator(pitch, yaw, roll)
    return rotator


def get_hinge_offset(component):
    try:
        origin, extent = component.get_local_bounds()
        x = origin.x + HINGE_SIDE * abs(extent.x)
        return unreal.Vector(x, origin.y, origin.z)
    except Exception:
        return unreal.Vector(0, 0, 0)


def rotated_location_about_hinge(closed_location, hinge_offset, angle_degrees):
    radians = math.radians(angle_degrees)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    rotated_hinge_x = hinge_offset.x * cos_a - hinge_offset.y * sin_a
    rotated_hinge_y = hinge_offset.x * sin_a + hinge_offset.y * cos_a
    return unreal.Vector(
        closed_location.x + hinge_offset.x - rotated_hinge_x,
        closed_location.y + hinge_offset.y - rotated_hinge_y,
        closed_location.z,
    )


def get_component_world_location(component, fallback_actor):
    try:
        return component.get_component_location()
    except Exception:
        try:
            return component.get_world_location()
        except Exception:
            return fallback_actor.get_actor_location()


def get_component_bounds_origin(component):
    try:
        bounds = component.bounds
        return bounds.origin
    except Exception:
        pass
    try:
        origin, _extent = component.get_local_bounds()
        return origin
    except Exception:
        return None


def get_static_mesh(component):
    try:
        return component.static_mesh
    except Exception:
        try:
            return component.get_editor_property("static_mesh")
        except Exception:
            return None


def set_relative_location(component, location):
    moved = False
    if hasattr(component, "set_relative_location"):
        for args in (
            (location, False, None, True),
            (location, False, True),
            (location,),
        ):
            try:
                component.set_relative_location(*args)
                moved = True
                break
            except TypeError:
                pass
            except Exception:
                break
    if not moved:
        try:
            component.set_editor_property("relative_location", location)
            moved = True
        except Exception:
            pass

    if moved:
        for method_name in ("update_component_to_world", "mark_render_transform_dirty", "mark_render_state_dirty"):
            try:
                method = getattr(component, method_name)
                method()
            except Exception:
                pass


def set_relative_rotation(component, rotation):
    moved = False
    if hasattr(component, "set_relative_rotation"):
        for args in (
            (rotation, False, None, True),
            (rotation, False, True),
            (rotation,),
        ):
            try:
                component.set_relative_rotation(*args)
                moved = True
                break
            except TypeError:
                pass
            except Exception:
                break
    if not moved:
        try:
            component.set_editor_property("relative_rotation", rotation)
            moved = True
        except Exception:
            pass

    if moved:
        for method_name in ("update_component_to_world", "mark_render_transform_dirty", "mark_render_state_dirty"):
            try:
                method = getattr(component, method_name)
                method()
            except Exception:
                pass


def make_actor_key(actor):
    label = safe_actor_label(actor)
    location = actor.get_actor_location()
    return "%s|%s|%0.1f|%0.1f|%0.1f" % (
        actor.get_class().get_path_name() if actor.get_class() else actor.get_name(),
        label,
        location.x,
        location.y,
        location.z,
    )


def safe_actor_label(actor):
    try:
        return actor.get_actor_label()
    except Exception:
        return actor.get_name()


try:
    unreal._codex_auto_door_system.stop()
except Exception:
    pass

unreal._codex_auto_door_system = _AutoDoorSystem()
unreal._codex_auto_door_system.start()
