"""Engage Warp: A split color warping effect.

Source: https://www.shadertoy.com/view/3syBDc
Author: THWeaver
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Engage Warp by Logan Apple (Twitter: @loganapple540)
// https://www.shadertoy.com/view/3syBDc
// MIT License

#define TIME_DELAY 0.1
#define DISPLACEMENT 0.5

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord / iResolution.xy - DISPLACEMENT;

    // Pixel color
    vec3 col;
    
    // Length of normalized and translated frag coord
    float len;
    for (int i = 0; i < 3; ++i) {
        len = length(uv);
        uv *= 1.0 + 2.0 / len * (cos(iTime) + 1.0) * abs(cos(0.5 * len - iTime));
        len *= mix(1.0, TIME_DELAY * sin(iTime * len), cos(iTime));
        
        col = vec3(
            i == 0 ? 0.1 / length(mod(uv, 1.0) - DISPLACEMENT) : col.r, 
            i == 1 ? 0.1 / length(mod(uv, 1.0) - DISPLACEMENT) : col.g, 
            i == 2 ? 0.1 / length(mod(uv, 1.0) - DISPLACEMENT) : col.b);
    }

    // Output to screen
    fragColor = vec4(col, 1.0);
}'''


class StEngageWarp(GlslSource):
    name = 'st_engage_warp'
    label = 'Engage Warp'
    description = 'A split color warping effect.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
