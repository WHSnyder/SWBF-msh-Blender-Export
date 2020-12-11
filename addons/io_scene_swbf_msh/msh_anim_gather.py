""" Extracts an animation from the action attached to the armature."""

import bpy
import math
from enum import Enum
from typing import List, Set, Dict, Tuple
from itertools import zip_longest
from .msh_model import *
from .msh_model_utilities import *
from .msh_utilities import *
from .msh_model_gather import *
from .crc import to_crc



def get_keyed_bones(action: bpy.types.Action, armature: bpy.types.Armature) -> List[Tuple[str,int]]:
    '''Iterates through the fcurves of the action, 
    determines which bones are actually animated, and returns
    a list of their names and crcs.'''

    used_bones = set()

    for fcurve in action.fcurves:

        data_path = fcurve.data_path

        if data_path.startswith("pose.bones"):

            bone_name = data_path.split("\"")[1]

            if bone_name in armature.pose.bones:
                used_bones.add(bone_name)

    return [(bone_name, to_crc(bone_name)) for bone_name in used_bones]





def extract_anim(armature: bpy.types.Armature, root_name: str) -> Animation:
    """Extracts an Animation from the locations and rotations of each keyed bone
    in the Action."""

    if armature.animation_data is None:
        raise Exception("Armature does not have animation data!")

    if armature.animation_data.action is None:
        raise Exception("Armature does not have an action currently attached!")


    action = armature.animation_data.action
    keyed_bones = get_keyed_bones(action, armature)


    anim = Animation();

    framerange = action.frame_range
    anim.start_index = math.floor(framerange.x)
    anim.end_index = math.ceil(framerange.y)


    # Init dummy frames for root object    
    root_crc = to_crc(root_name)
    anim.bone_frames[root_crc] = ([], [])

    # Init frames for each keyed bone
    for _, bone_crc in keyed_bones:
        anim.bone_frames[bone_crc] = ([], [])


    for frame in range(anim.start_index, anim.end_index + 1):
        
        bpy.context.scene.frame_set(frame)

        # Add in dummy frames for the scene root
        rframe_dummy = RotationFrame(frame, convert_rotation_space(Quaternion()))
        tframe_dummy = TranslationFrame(frame, Vector((0.0,0.0,0.0)))

        anim.bone_frames[root_crc][0].append(tframe_dummy)
        anim.bone_frames[root_crc][1].append(rframe_dummy)


        for bone_name, bone_crc in keyed_bones:

            bone = armature.pose.bones[bone_name]

            transform = bone.matrix

            if bone.parent:
                transform = bone.parent.matrix.inverted() @ transform
 
            loc, rot, _ = transform.decompose()

            rframe = RotationFrame(frame, convert_rotation_space(rot))
            tframe = TranslationFrame(frame, convert_vector_space(loc))

            anim.bone_frames[bone_crc][0].append(tframe)
            anim.bone_frames[bone_crc][1].append(rframe)


    bpy.context.scene.frame_set(0)

    return anim
