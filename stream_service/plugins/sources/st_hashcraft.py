"""Hashcraft: Watch the Intricate Patterns Emerge over Time.

Source: https://www.shadertoy.com/view/Md3BW2
Author: James_Harnett
License: Unlicense
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // LICENSE: http://unlicense.org/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// LICENSE: http://unlicense.org/
void mainImage(out vec4 O,vec2 U){
uvec2 p = uvec2(U + 9e3) + uint(iFrame);
p ^= p << (p.x + p.y >> 11);
uint n = (p.x & p.y) - p.x;
n ^= n << 21;
O += sqrt(float(n ^ n << (n >> 27)) / 4e9);}'''


class StHashcraft(GlslSource):
    name = 'st_hashcraft'
    label = 'Hashcraft'
    description = 'Watch the Intricate Patterns Emerge over Time.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
