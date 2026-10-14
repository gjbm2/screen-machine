"""[290 min char] Hyper swirls: quick min shader sketch

Source: https://www.shadertoy.com/view/tt3fRj
Author: public_int_i
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0, public domain, freeware
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0, public domain, freeware

void mainImage(out vec4 o, in vec2 u)
{
    vec2 c = (u*2.-iResolution.xy)/iResolution.y;//2d world coordinate
    o -= o;
    for (float i = 1.; i < 100.; i += .1) {
        //4d point
        vec4 p = cos(i*vec4(.87,.276,.587,1.1));
        float a = iTime+length(p.zy);
        p.zw *= mat2(cos(a),-sin(a),sin(a),cos(a));
        
        //4d to 3d projection
        p.xyz /= p.w+2.;
        
        //additively draw points + 3d to 2d projection
        p.z += 2.;
        o += clamp(1./p.z-length(c-p.xy/p.z)*20.,0.,.1);
    }
}'''


class St290MinCharHyperSwirls(GlslSource):
    name = 'st_290_min_char_hyper_swirls'
    label = '[290 min char] Hyper swirls'
    description = 'quick min shader sketch'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
