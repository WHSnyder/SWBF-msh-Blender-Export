""" Gathers the Blender objects from the current scene and returns them as a list of
    Model objects. """

import bpy
import math
from enum import Enum
from typing import List, Set, Dict, Tuple, Set
from itertools import zip_longest
from .msh_model import *
from .msh_model_utilities import *
from .msh_utilities import *

SKIPPED_OBJECT_TYPES = {"LATTICE", "CAMERA", "LIGHT", "SPEAKER", "LIGHT_PROBE"}
MESH_OBJECT_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT", "GPENCIL"}
MAX_MSH_VERTEX_COUNT = 32767

def gather_models(apply_modifiers: bool, export_target: str, skeleton_only: bool) -> Tuple[List[Model], bpy.types.Object]:
    """ Gathers the Blender objects from the current scene and returns them as a list of
        Model objects.  Also returns the armature if one is found among the selected models, in case
        animations are to be exported."""

    depsgraph = bpy.context.evaluated_depsgraph_get()
    parents = create_parents_set()

    models_list: List[Model] = []

    armature_found = None

    for uneval_obj in select_objects(export_target):
        if uneval_obj.type in SKIPPED_OBJECT_TYPES and uneval_obj.name not in parents:
            continue

        if apply_modifiers:
            obj = uneval_obj.evaluated_get(depsgraph)
        else:
            obj = uneval_obj 

        check_for_bad_lod_suffix(obj)

        if obj.type == "ARMATURE":
            models_list += expand_armature(obj)
            armature_found = obj
            continue

        model = Model()
        model.name = obj.name
        model.model_type = get_model_type(obj) if not skeleton_only else ModelType.NULL
        model.hidden = get_is_model_hidden(obj)

        transform = obj.matrix_local

        if obj.parent_bone:
            model.parent = obj.parent_bone

            # matrix_local, when called on an armature child also parented to a bone, appears to be broken.
            # At the very least, the results contradict the docs...  
            armature_relative_transform = obj.parent.matrix_world.inverted() @ obj.matrix_world
            transform = obj.parent.data.bones[obj.parent_bone].matrix_local.inverted() @ armature_relative_transform 

        else:
            if obj.parent is not None:
                if obj.parent.type == "ARMATURE":
                    # Reparent since we exclude armature objects
                    armature_parent = obj.parent.parent
                    model.parent = armature_parent.name if armature_parent is not None else ""
                else:
                    model.parent = obj.parent.name

        local_translation, local_rotation, _ = transform.decompose()
        model.transform.rotation = convert_rotation_space(local_rotation)  
        model.transform.translation = convert_vector_space(local_translation)


        if model.model_type == ModelType.STATIC or model.model_type == ModelType.SKIN:

            mesh = obj.to_mesh()
            model.geometry = create_mesh_geometry(mesh, obj.vertex_groups)
            obj.to_mesh_clear()

            _, _, world_scale = obj.matrix_world.decompose()
            world_scale = convert_scale_space(world_scale)
            scale_segments(world_scale, model.geometry)
                
            for segment in model.geometry:
                if len(segment.positions) > MAX_MSH_VERTEX_COUNT:
                    raise RuntimeError(f"Object '{obj.name}' has resulted in a .msh geometry segment that has "
                                       f"more than {MAX_MSH_VERTEX_COUNT} vertices! Split the object's mesh up "
                                       f"and try again!")
            if obj.vertex_groups:
                model.bone_map = [group.name for group in obj.vertex_groups]


        if get_is_collision_primitive(obj):
            model.collisionprimitive = get_collision_primitive(obj)


        models_list.append(model)


    return (models_list, armature_found)


def create_parents_set() -> Set[str]:
    """ Creates a set with the names of the Blender objects from the current scene
        that have at least one child. """
        
    parents = set()

    for obj in bpy.context.scene.objects:
        if obj.parent is not None:
            parents.add(obj.parent.name)

    return parents


def create_mesh_geometry(mesh: bpy.types.Mesh, has_weights: bool) -> List[GeometrySegment]:
    """ Creates a list of GeometrySegment objects from a Blender mesh.
        Does NOT create triangle strips in the GeometrySegment however. """

    if mesh.has_custom_normals:
        mesh.calc_normals_split()

    mesh.validate_material_indices()
    mesh.calc_loop_triangles()

    material_count = max(len(mesh.materials), 1)

    segments: List[GeometrySegment] = [GeometrySegment() for i in range(material_count)]
    vertex_cache = [dict() for i in range(material_count)]
    vertex_remap: List[Dict[Tuple[int, int], int]] = [dict() for i in range(material_count)]
    polygons: List[Set[int]] = [set() for i in range(material_count)]

    if mesh.vertex_colors.active is not None:
        for segment in segments:
            segment.colors = []

    if has_weights:
        for segment in segments:
            segment.weights = []

    for segment, material in zip(segments, mesh.materials):
        segment.material_name = material.name

    def add_vertex(material_index: int, vertex_index: int, loop_index: int, use_smooth_normal: bool, face_normal: Vector) -> int:
        nonlocal segments, vertex_remap

        vertex_cache_miss_index = -1
        segment = segments[material_index]
        cache = vertex_cache[material_index]
        remap = vertex_remap[material_index]

        vertex_normal: Vector

        if use_smooth_normal or mesh.use_auto_smooth:
            if mesh.has_custom_normals:
                vertex_normal = Vector( mesh.loops[loop_index].normal )
            else:
                vertex_normal = Vector( mesh.vertices[vertex_index].normal )
        else:
            vertex_normal = Vector(face_normal)

        def get_cache_vertex():
            yield mesh.vertices[vertex_index].co.x
            yield mesh.vertices[vertex_index].co.y
            yield mesh.vertices[vertex_index].co.z

            yield vertex_normal.x
            yield vertex_normal.y
            yield vertex_normal.z

            if mesh.uv_layers.active is not None:
                yield mesh.uv_layers.active.data[loop_index].uv.x
                yield mesh.uv_layers.active.data[loop_index].uv.y

            if segment.colors is not None:
                for v in mesh.vertex_colors.active.data[loop_index].color:
                    yield v

            if segment.weights is not None:
                for v in mesh.vertices[vertex_index].groups:
                    yield v.group
                    yield v.weight

        vertex_cache_entry = tuple(get_cache_vertex())
        cached_vertex_index = cache.get(vertex_cache_entry, vertex_cache_miss_index)

        if cached_vertex_index != vertex_cache_miss_index:
            remap[(vertex_index, loop_index)] = cached_vertex_index

            return cached_vertex_index

        new_index: int = len(segment.positions)
        cache[vertex_cache_entry] = new_index
        remap[(vertex_index, loop_index)] = new_index

        segment.positions.append(convert_vector_space(mesh.vertices[vertex_index].co))
        segment.normals.append(convert_vector_space(vertex_normal))

        if mesh.uv_layers.active is None:
            segment.texcoords.append(Vector((0.0, 0.0)))
        else:
            segment.texcoords.append(mesh.uv_layers.active.data[loop_index].uv.copy())

        if segment.colors is not None:
            segment.colors.append(list(mesh.vertex_colors.active.data[loop_index].color))

        if segment.weights is not None:
            groups = mesh.vertices[vertex_index].groups
           
            segment.weights.append([VertexWeight(v.weight, v.group) for v in groups])

        return new_index


    for tri in mesh.loop_triangles:
        polygons[tri.material_index].add(tri.polygon_index)
        segments[tri.material_index].triangles.append([
            add_vertex(tri.material_index, tri.vertices[0], tri.loops[0], tri.use_smooth, tri.normal),
            add_vertex(tri.material_index, tri.vertices[1], tri.loops[1], tri.use_smooth, tri.normal),
            add_vertex(tri.material_index, tri.vertices[2], tri.loops[2], tri.use_smooth, tri.normal)])

    for segment, remap, polys in zip(segments, vertex_remap, polygons):
        for poly_index in polys:
            poly = mesh.polygons[poly_index]

            segment.polygons.append([remap[(v, l)] for v, l in zip(poly.vertices, poly.loop_indices)])

    return segments

def get_model_type(obj: bpy.types.Object) -> ModelType:
    """ Get the ModelType for a Blender object. """

    if obj.type in MESH_OBJECT_TYPES:
        if obj.vertex_groups:
            return ModelType.SKIN
        else:
            return ModelType.STATIC

    return ModelType.NULL

def get_is_model_hidden(obj: bpy.types.Object) -> bool:
    """ Gets if a Blender object should be marked as hidden in the .msh file. """

    name = obj.name.lower()

    if name.startswith("sv_"):
        return True
    if name.startswith("p_"):
        return True
    if name.startswith("collision"):
        return True

    if obj.type not in MESH_OBJECT_TYPES:
        return True

    if name.endswith("_lod2"):
        return True
    if name.endswith("_lod3"):
        return True
    if name.endswith("_lowrez"):
        return True
    if name.endswith("_lowres"):
        return True

    return False

def get_is_collision_primitive(obj: bpy.types.Object) -> bool:
    """ Gets if a Blender object represents a collision primitive. """

    name = obj.name.lower()

    return name.startswith("p_")

def get_collision_primitive(obj: bpy.types.Object) -> CollisionPrimitive:
    """ Gets the CollisionPrimitive of an object or raises an error if
        it can't. """

    primitive = CollisionPrimitive()
    primitive.shape = get_collision_primitive_shape(obj)

    if primitive.shape == CollisionPrimitiveShape.SPHERE:
        # Tolerate a 5% difference to account for icospheres with 2 subdivisions.
        if not (math.isclose(obj.dimensions[0], obj.dimensions[1], rel_tol=0.05) and
                math.isclose(obj.dimensions[0], obj.dimensions[2], rel_tol=0.05)):
            raise RuntimeError(f"Object '{obj.name}' is being used as a sphere collision "
                               f"primitive but it's dimensions are not uniform!")

        primitive.radius = max(obj.dimensions[0], obj.dimensions[1], obj.dimensions[2]) * 0.5
    elif primitive.shape == CollisionPrimitiveShape.CYLINDER:
        if not math.isclose(obj.dimensions[0], obj.dimensions[1], rel_tol=0.001):
            raise RuntimeError(f"Object '{obj.name}' is being used as a cylinder collision "
                               f"primitive but it's X and Y dimensions are not uniform!")
        primitive.radius = obj.dimensions[0] * 0.5
        primitive.height = obj.dimensions[2]
    elif primitive.shape == CollisionPrimitiveShape.BOX:
        primitive.radius = obj.dimensions[0] * 0.5
        primitive.height = obj.dimensions[2] * 0.5
        primitive.length = obj.dimensions[1] * 0.5

    return primitive

def get_collision_primitive_shape(obj: bpy.types.Object) -> CollisionPrimitiveShape:
    """ Gets the CollisionPrimitiveShape of an object or raises an error if
        it can't. """

    name = obj.name.lower()

    if "sphere" in name or "sphr" in name or "spr" in name:
        return CollisionPrimitiveShape.SPHERE
    if "cylinder" in name or "cyln" in name or "cyl" in name:
        return CollisionPrimitiveShape.CYLINDER
    if "box" in name or "cube" in name or "cuboid" in name:
        return CollisionPrimitiveShape.BOX

    raise RuntimeError(f"Object '{obj.name}' has no primitive type specified in it's name!")

def check_for_bad_lod_suffix(obj: bpy.types.Object):
    """ Checks if the object has an LOD suffix that is known to be ignored by  """

    name = obj.name.lower()
    failure_message = f"Object '{obj.name}' has unknown LOD suffix at the end of it's name!"

    if name.endswith("_lod1"):
        raise RuntimeError(failure_message)
    
    for i in range(4, 10):
        if name.endswith(f"_lod{i}"):
            raise RuntimeError(failure_message)

def select_objects(export_target: str) -> List[bpy.types.Object]:
    """ Returns a list of objects to export. """

    if export_target == "SCENE" or not export_target in {"SELECTED", "SELECTED_WITH_CHILDREN"}:
        return list(bpy.context.scene.objects)

    objects = list(bpy.context.selected_objects)
    added = {obj.name for obj in objects}

    if export_target == "SELECTED_WITH_CHILDREN":
        children = []

        def add_children(parent):
            nonlocal children
            nonlocal added

            for obj in bpy.context.scene.objects:
                if obj.parent == parent and obj.name not in added:
                    children.append(obj)
                    added.add(obj.name)

                    add_children(obj)

        
        for obj in objects:
            add_children(obj)

        objects = objects + children

    parents = []

    for obj in objects:
        parent = obj.parent

        while parent is not None:
            if parent.name not in added:
                parents.append(parent)
                added.add(parent.name)

            parent = parent.parent

    return objects + parents



def expand_armature(obj: bpy.types.Object) -> List[Model]:
    bones: List[Model] = []

    for bone in obj.data.bones:
        model = Model()

        transform = bone.matrix_local

        if bone.parent:
            transform = bone.parent.matrix_local.inverted() @ transform
            model.parent = bone.parent.name
        else:
            model.parent = obj.parent.name
            for child_obj in obj.children:
                if child_obj.vertex_groups and not get_is_model_hidden(obj) and not obj.parent_bone:
                    model.parent = child_obj.name


        local_translation, local_rotation, _ = transform.decompose()

        model.model_type = ModelType.BONE
        model.name = bone.name
        model.transform.rotation = convert_rotation_space(local_rotation)
        model.transform.translation = convert_vector_space(local_translation)

        bones.append(model)

    return bones
