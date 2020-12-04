""" Contains functions for extracting a scene from a .msh file"""

from itertools import islice
from typing import Dict
from .msh_scene import Scene
from .msh_model import *
from .msh_material import *
from .msh_reader import Reader
from .msh_utilities import *

from .crc import *

model_counter = 0

mndx_remap = {}


def read_scene(input_file) -> Scene:

    scene = Scene()
    scene.models = []
    scene.materials = {}

    global mndx_remap
    mndx_remap = {}

    global model_counter
    model_counter = 0

    with Reader(file=input_file, debug=True) as hedr:

        while hedr.could_have_child():

            next_header = hedr.peak_next_header()

            if "MSH2" in next_header:

                with hedr.read_child() as msh2:

                    materials_list = []

                    while (msh2.could_have_child()):

                        next_header = msh2.peak_next_header()

                        if "SINF" in next_header:
                            with msh2.read_child() as sinf:
                                pass

                        elif "MATL" in next_header:
                            with msh2.read_child() as matl:
                                materials_list += _read_matl_and_get_materials_list(matl)
                                for i,mat in enumerate(materials_list):
                                    scene.materials[mat.name] = mat

                        elif "MODL" in next_header:
                            while ("MODL" in msh2.peak_next_header()):
                                with msh2.read_child() as modl:
                                    scene.models.append(_read_modl(modl, materials_list))

                        else:
                            msh2.skip_bytes(1)

            elif "SKL2" in next_header:
                with hedr.read_child() as skl2:
                    num_bones = skl2.read_u32()
                    scene.skeleton = [skl2.read_u32(5)[0] for i in range(num_bones)]
                
            elif "ANM2" in next_header:
                with hedr.read_child() as anm2:
                    _read_anm2(anm2, scene.models)

            else:
                hedr.skip_bytes(1)


    if scene.skeleton:
        print("Skeleton models: ")
        for model in scene.models:
            for i in range(len(scene.skeleton)):                
                if crc(model.name) == scene.skeleton[i]:
                    print("\t" + model.name)
                    if model.model_type == ModelType.SKIN:
                        scene.skeleton.pop(i)
                    break


    for model in scene.models:
        if model.geometry:
            for seg in model.geometry:
                if seg.weights:
                    for weight_set in seg.weights:
                        for vweight in weight_set:
                            vweight.bone = mndx_remap[vweight.bone]
                    
    return scene


def _read_matl_and_get_materials_list(matl: Reader) -> List[Material]:
    materials_list: List[Material] = []

    num_mats = matl.read_u32()

    for _ in range(num_mats):
        with matl.read_child() as matd:
            materials_list.append(_read_matd(matd))

    return materials_list



def _read_matd(matd: Reader) -> Material:

    mat = Material()

    while matd.could_have_child():

        next_header = matd.peak_next_header()

        if "NAME" in next_header:
            with matd.read_child() as name:
                mat.name = name.read_string()

        elif "DATA" in next_header:
            with matd.read_child() as data:
                data.read_f32(4) # Diffuse Color (Seams to get ignored by modelmunge)
                mat.specular_color = data.read_f32(4)
                data.read_f32(4) # Ambient Color (Seams to get ignored by modelmunge and Zero(?))
                data.read_f32()  # Specular Exponent/Decay (Gets ignored by RedEngine in SWBFII for all known materials)    
    
        elif "ATRB" in next_header:
            with matd.read_child() as atrb:
                mat.flags = atrb.read_u8()
                mat.rendertype = atrb.read_u8()
                mat.data = atrb.read_u8(2)

        elif "TX0D" in next_header:
            with matd.read_child() as tx0d:
                mat.texture0 = tx0d.read_string()

        elif "TX1D" in next_header:
            with matd.read_child() as tx1d:
                mat.texture1 = tx1d.read_string()

        elif "TX2D" in next_header:
            with matd.read_child() as tx2d:
                mat.texture2 = tx2d.read_string()

        elif "TX3D" in next_header:
            with matd.read_child() as tx3d:
                mat.texture3 = tx3d.read_string()

        else:
            matd.skip_bytes(1)

    return mat


def _read_modl(modl: Reader, materials_list: List[Material]) -> Model:

    model = Model()

    while modl.could_have_child():

        next_header = modl.peak_next_header()

        if "MTYP" in next_header:
            with modl.read_child() as mtyp:
                model.model_type = ModelType(mtyp.read_u32())

        elif "MNDX" in next_header:
            with modl.read_child() as mndx:
                index = mndx.read_u32()

                global model_counter
                print(mndx.indent + "MNDX doesn't match counter, expected: {} found: {}".format(model_counter, index))

                global mndx_remap
                mndx_remap[index] = model_counter

                model_counter += 1

        elif "NAME" in next_header:
            with modl.read_child() as name:
                model.name = name.read_string()

        elif "PRNT" in next_header:
            with modl.read_child() as prnt:
                model.parent = prnt.read_string()

        elif "FLGS" in next_header:
            with modl.read_child() as flgs:
                model.hidden = flgs.read_u32()

        elif "TRAN" in next_header:
            with modl.read_child() as tran:
                model.transform = _read_tran(tran)

        elif "GEOM" in next_header:
            model.geometry = []
            envelope = []

            with modl.read_child() as geom:

                while geom.could_have_child():
                    next_header_geom = geom.peak_next_header()

                    if "SEGM" in next_header_geom:
                        with geom.read_child() as segm:
                           model.geometry.append(_read_segm(segm, materials_list))

                    elif "ENVL" in next_header_geom:
                        with geom.read_child() as envl:
                            num_indicies = envl.read_u32()
                            envelope += [envl.read_u32() for _ in range(num_indicies)]
                    
                    else:
                        geom.skip_bytes(1)
                        #with geom.read_child() as null:
                        #pass

            for seg in model.geometry:
                if seg.weights and envelope:
                    for weight_set in seg.weights:
                        for i in range(len(weight_set)):
                            vertex_weight = weight_set[i]
                            index = vertex_weight.bone
                            weight_set[i] = VertexWeight(vertex_weight.weight, envelope[vertex_weight.bone])

        elif "SWCI" in next_header:
            prim = CollisionPrimitive()
            with modl.read_child() as swci:
                prim.shape = CollisionPrimitiveShape(swci.read_u32())
                prim.radius = swci.read_f32()
                prim.height = swci.read_f32()
                prim.length = swci.read_f32()
            model.collisionprimitive = prim

        else:
            modl.skip_bytes(1)

    print(modl.indent + "Read model " + model.name + " of type: " + str(model.model_type)[10:])

    return model


def _read_tran(tran: Reader) -> ModelTransform:

    xform = ModelTransform()

    tran.skip_bytes(12) #ignore scale

    rot = tran.read_f32(4)
    xform.rotation = Quaternion((rot[3], rot[0], rot[1], rot[2]))
    xform.translation = Vector(tran.read_f32(3))

    return xform


def _read_segm(segm: Reader, materials_list: List[Material]) -> GeometrySegment:

    geometry_seg = GeometrySegment()

    while segm.could_have_child():

        next_header = segm.peak_next_header()

        if "MATI" in next_header:
            with segm.read_child() as mati:
                geometry_seg.material_name = materials_list[mati.read_u32()].name

        elif "POSL" in next_header:
            with segm.read_child() as posl:
                num_positions = posl.read_u32()

                for _ in range(num_positions):
                    geometry_seg.positions.append(Vector(posl.read_f32(3)))

        elif "NRML" in next_header:
            with segm.read_child() as nrml:
                num_normals = nrml.read_u32()
                
                for _ in range(num_positions):
                    geometry_seg.normals.append(Vector(nrml.read_f32(3))) 

        elif "CLRL" in next_header:
            geometry_seg.colors = []

            with segm.read_child() as clrl:
                num_colors = clrl.read_u32()

                for _ in range(num_colors):
                    geometry_seg.colors += unpack_color(clrl.read_u32())

        elif "UV0L" in next_header:
            with segm.read_child() as uv0l:
                num_texcoords = uv0l.read_u32()

                for _ in range(num_texcoords):
                    geometry_seg.texcoords.append(Vector(uv0l.read_f32(2))) 

        elif "NDXL" in next_header:
            with segm.read_child() as ndxl:
                num_polygons = ndxl.read_u32()

                for _ in range(num_polygons):
                    polygon = ndxl.read_u16(ndxl.read_u16())
                    geometry_seg.polygons.append(polygon)

        elif "NDXT" in next_header:
            with segm.read_child() as ndxt:
                num_tris = ndxt.read_u32()

                for _ in range(num_tris):
                    geometry_seg.triangles.append(ndxt.read_u16(3))

        elif "STRP" in next_header:
            strips : List[List[int]] = []

            with segm.read_child() as strp:
                num_indicies = strp.read_u32()

                num_indicies_read = 0

                curr_strip = []
                previous_flag = False

                if num_indicies > 0:
                    index, index1 = strp.read_u16(2)
                    curr_strip = [index & 0x7fff, index1 & 0x7fff]
                    num_indicies_read += 2

                for i in range(num_indicies - 2):
                    index = strp.read_u16(1)

                    if index & 0x8000 > 0:
                        index = index & 0x7fff

                        if previous_flag:
                            previous_flag = False
                            curr_strip.append(index)
                            strips.append(curr_strip[:-2])
                            curr_strip = curr_strip[-2:]
                            continue
                        else:
                            previous_flag = True

                    else:
                        previous_flag = False
                     
                    curr_strip.append(index)

            geometry_seg.triangle_strips = strips

            #if segm.read_u16 != 0: #trailing 0 bug https://schlechtwetterfront.github.io/ze_filetypes/msh.html#STRP
            #    segm.skip_bytes(-2)

        elif "WGHT" in next_header:
            with segm.read_child() as wght:
                
                geometry_seg.weights = []
                num_weights = wght.read_u32()

                for _ in range(num_weights):
                    weight_set = []
                    for _ in range(4):
                        index = wght.read_u32()
                        value = wght.read_f32()

                        if value > 0.000001:
                            weight_set.append(VertexWeight(value,index))

                    geometry_seg.weights.append(weight_set)

        else:
            segm.skip_bytes(1)

    return geometry_seg



def _read_anm2(anm2: Reader, models):

    hash_dict = {}
    for model in models:
        hash_dict[crc(model.name)] = model.name

    while anm2.could_have_child():

        next_header = anm2.peak_next_header()

        if "CYCL" in next_header:
            with anm2.read_child() as cycl:
                pass

        elif "KFR3" in next_header:
            with anm2.read_child() as kfr3:

                num_bones = kfr3.read_u32()

                for _ in range(num_bones):

                    kfr3.read_u32()

                    frametype = kfr3.read_u32()

                    num_loc_frames = kfr3.read_u32()
                    num_rot_frames = kfr3.read_u32()

                    kfr3.skip_bytes(16 * num_loc_frames + 20 * num_rot_frames)


