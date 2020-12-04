""" Gathers the Blender objects from the current scene and returns them as a list of
    Model objects. """

import bpy
import bmesh
import math
from enum import Enum
from typing import List, Set, Dict, Tuple
from itertools import zip_longest
from .msh_scene import Scene
from .msh_model import *
from .msh_model_utilities import *
from .msh_utilities import *
from .msh_model_gather import *
from .crc import *

import os



#def import_anim(scene : Scene):




def refined_skeleton_to_armature(refined_skeleton : List[Model], model_map):

    armature = bpy.data.armatures.new("skeleton")
    armature_obj = bpy.data.objects.new("skeleton", armature)

    bpy.context.view_layer.active_layer_collection.collection.objects.link(armature_obj)
    armature_obj.select_set(True)

    bpy.context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')

    for bone in refined_skeleton:

        edit_bone = armature.edit_bones.new(bone.name)

        if bone.parent:
            edit_bone.parent = armature.edit_bones[bone.parent]

        edit_bone.head = model_map[bone.name].matrix_world.translation

        bone_children = [b for b in get_model_children(bone, refined_skeleton)]
        
        if bone_children:
            edit_bone.tail = Vector((-0.00001,0.0,0.0))
            for bone_child in bone_children:
                edit_bone.tail += model_map[bone_child.name].matrix_world.translation
            edit_bone.tail = edit_bone.tail / len(bone_children) 
        else:
            edit_bone.tail = model_map[bone.name].matrix_world @ Vector((-0.2,0.0,0.0))


    bpy.ops.object.mode_set(mode='OBJECT')
    armature_obj.select_set(True)
    bpy.context.view_layer.update() 

    return armature_obj






def extract_refined_skeleton(scene: Scene):

    model_dict = {}
    skeleton_models = []


    for model in scene.models:
        model_dict[model.name] = model

        if model.geometry:
            for seg in model.geometry:
                if seg.weights:
                    for weight_set in seg.weights:
                        for weight in weight_set:
                            model_weighted_to = scene.models[weight.bone]

                            if crc(model_weighted_to.name) not in scene.skeleton:
                                scene.skeleton.append(crc(model_weighted_to.name))
                                print("Found additional bone: " + model_weighted_to.name)
                    
    for model in scene.models:
        if crc(model.name) in scene.skeleton:
            skeleton_models.append(model)


    refined_skeleton_models = []

    for bone in skeleton_models:

        if bone.parent:

            curr_ancestor = model_dict[bone.parent]
            stacked_transform = model_transform_to_matrix(bone.transform)

            while True:

                if crc(curr_ancestor.name) in scene.skeleton or curr_ancestor.name == scene.models[0].name:
                    new_model = Model()
                    new_model.name = bone.name
                    print("Adding {} to refined skeleton...".format(bone.name))
                    new_model.parent = curr_ancestor.name if curr_ancestor.name != scene.models[0].name else ""

                    loc, rot, _ = stacked_transform.decompose()

                    new_model.transform.rotation = rot
                    new_model.transform.translation = loc
                    
                    refined_skeleton_models.append(new_model)
                    break

                else:
                    curr_ancestor = model_dict[curr_ancestor.parent]
                    stacked_transform = model_transform_to_matrix(curr_ancestor.transform) @ stacked_transform

    return sort_by_parent(refined_skeleton_models)                  









def extract_models(scene: Scene, materials_map):

    model_map = {}

    for model in sort_by_parent(scene.models):
        new_obj = None

        if model.model_type == ModelType.STATIC or model.model_type == ModelType.SKIN:  

            new_mesh = bpy.data.meshes.new(model.name)
            verts = []
            faces = []
            offset = 0

            mat_name = ""

            full_texcoords = []

            weights_offsets = {}

            for i,seg in enumerate(model.geometry):

                if i == 0:
                    mat_name = seg.material_name

                verts += [tuple(convert_vector_space(v)) for v in seg.positions]

                if seg.weights:
                    weights_offsets[offset] = seg.weights

                if seg.texcoords is not None:
                    full_texcoords += seg.texcoords
                else:
                    full_texcoords += [(0.0,0.0) for _ in range(len(seg.positions))]

                if seg.triangles:
                    faces += [tuple([ind + offset for ind in tri]) for tri in seg.triangles]
                else:
                    for strip in seg.triangle_strips:
                        for i in range(len(strip) - 2):
                            face = tuple([offset + strip[j] for j in range(i,i+3)])
                            print("strip face: " + str(face))
                            faces.append(face)

                offset += len(seg.positions)

            new_mesh.from_pydata(verts, [], faces)
            new_mesh.update()
            new_mesh.validate()

            
            if full_texcoords:

                edit_mesh = bmesh.new()
                edit_mesh.from_mesh(new_mesh)

                uvlayer = edit_mesh.loops.layers.uv.verify()

                for edit_mesh_face in edit_mesh.faces:
                    mesh_face = faces[edit_mesh_face.index]

                    for i,loop in enumerate(edit_mesh_face.loops):

                        texcoord = full_texcoords[mesh_face[i]]
                        loop[uvlayer].uv = tuple([texcoord.x, texcoord.y])

                edit_mesh.to_mesh(new_mesh)
                edit_mesh.free() 
            
            new_obj = bpy.data.objects.new(new_mesh.name, new_mesh)


            vertex_groups_indicies = {}

            for offset in weights_offsets:
                for i, weight_set in enumerate(weights_offsets[offset]):
                    for weight in weight_set:
                        index = weight.bone

                        if index not in vertex_groups_indicies:
                            model_name = scene.models[index].name
                            vertex_groups_indicies[index] = new_obj.vertex_groups.new(name=model_name)

                        vertex_groups_indicies[index].add([offset + i], weight.weight, 'ADD')

            '''
            Assign Materials - will do per segment later...
            '''
            if mat_name:
                material = materials_map[mat_name]

                if new_obj.data.materials:
                    new_obj.data.materials[0] = material
                else:
                    new_obj.data.materials.append(material)
        
        else:

            new_obj = bpy.data.objects.new(model.name, None)
            new_obj.empty_display_size = 1
            new_obj.empty_display_type = 'PLAIN_AXES' 


        model_map[model.name] = new_obj

        if model.parent:
            new_obj.parent = model_map[model.parent]

        new_obj.location = convert_vector_space(model.transform.translation)
        new_obj.rotation_mode = "QUATERNION"
        new_obj.rotation_quaternion = convert_rotation_space(model.transform.rotation)

        bpy.context.collection.objects.link(new_obj)


    return model_map



def extract_materials(folder_path: str, scene: Scene) -> Dict[str,bpy.types.Material]:

    extracted_materials = {}

    for material_name in scene.materials.keys():

        new_mat = bpy.data.materials.new(name=material_name)
        new_mat.use_nodes = True
        bsdf = new_mat.node_tree.nodes["Principled BSDF"]

        tex_path_def = os.path.join(folder_path, scene.materials[material_name].texture0)
        tex_path_alt = os.path.join(folder_path, "PC", scene.materials[material_name].texture0)

        tex_path = tex_path_def if os.path.exists(tex_path_def) else tex_path_alt

        if os.path.exists(tex_path):
            texImage = new_mat.node_tree.nodes.new('ShaderNodeTexImage')
            texImage.image = bpy.data.images.load(tex_path)
            new_mat.node_tree.links.new(bsdf.inputs['Base Color'], texImage.outputs['Color'])

        extracted_materials[material_name] = new_mat

    return extracted_materials



def extract_scene(filepath: str, scene: Scene):

    folder = os.path.join(os.path.dirname(filepath),"")
    matmap = extract_materials(folder, scene)

    model_map = extract_models(scene, matmap)

    skel = extract_refined_skeleton(scene)
    armature = refined_skeleton_to_armature(skel, model_map)

    reparent_obj = None
    for model in scene.models:
        if model.model_type == ModelType.SKIN:

            if model.parent:
                reparent_obj = model_map[model.parent]

            skin_obj = model_map[model.name]
            skin_obj.select_set(True)
            armature.select_set(True)
            bpy.context.view_layer.objects.active = armature

            bpy.ops.object.parent_clear(type='CLEAR')
            bpy.ops.object.parent_set(type='ARMATURE')

            skin_obj.select_set(False)
            armature.select_set(False)
            bpy.context.view_layer.objects.active = None

    if reparent_obj is not None:
        armature.select_set(True)
        reparent_obj.select_set(True)
        bpy.context.view_layer.objects.active = reparent_obj
        bpy.ops.object.parent_set(type='OBJECT')

        armature.select_set(False)
        reparent_obj.select_set(False)
        bpy.context.view_layer.objects.active = None


    if armature is not None:
        for bone in armature.data.bones:
            model_map[bone.name].select_set(True)
        bpy.ops.object.delete()

    for model in scene.models:
        if model.name in bpy.data.objects:
            obj = bpy.data.objects[model.name]
            if get_is_model_hidden(obj) and len(obj.children) == 0:
                obj.hide_set(True)















