"""Onward to Thule: Thule, the fabled northern-most land! This is like my previous Northern Journey and Northern Warbler shaders combined. It's also somewhat more colorful.

Source: https://www.shadertoy.com/view/7dy3R3
Author: TEttinger
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // CC0 licensed, do what thou wilt.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// CC0 licensed, do what thou wilt.

// change the seed to any not-too-huge float and the colors/shapes will change.
const float SEED = 420.69; // starts off nicely.
const vec3 COEFFS = fract((SEED + 23.4567) * vec3(0.8191725133961645, 0.6710436067037893, 0.5497004779019703)) + 0.5;

// what's different here is mostly how swayRandomized() incorporates the x, y, and z of seed and value for each component.
vec3 swayRandomized(vec3 seed, vec3 value)
{
    return sin(seed.xyz + value.zxy - cos(seed.zxy + value.yzx) + cos(seed.yzx + value.xyz));
}

// this function, if given steadily-increasing values in con, may return exponentially-rapidly-changing results.
// even though it should always return a vec3 with components between -1 and 1, we use it carefully.
vec3 cosmic(vec3 c, vec3 con)
{
    return (con
    + swayRandomized(c, con.yzx)
    + swayRandomized(c + 1.1, con.zxy)
    + swayRandomized(c + 2.2, con.xyz)) * 0.25;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord/iResolution.xy * 64.0 + swayRandomized(COEFFS.zxy, (iTime * 0.1875) * COEFFS.yzx).xy * 32.0;
    // aTime, s, and c could be uniforms in some engines.
    float aTime = iTime * 0.0625;
    vec3 adj = vec3(-1.11, 1.41, 1.61);
    vec3 s = (swayRandomized(vec3(34.0, 76.0, 59.0), aTime + adj)) * 0.25;
    vec3 c = (swayRandomized(vec3(27.0, 67.0, 45.0), aTime - adj)) * 0.25;
    vec3 con = vec3(0.0004375, 0.0005625, 0.0008125) * aTime + c * uv.x + s * uv.y;
    
    con = cosmic(COEFFS, con);
    con = cosmic(COEFFS + 1.618, con + COEFFS);
    
    fragColor = vec4(swayRandomized(COEFFS + 3.0, con * (3.14159265)) * 0.5 + 0.5,1.0);
}'''


class StOnwardToThule(GlslSource):
    name = 'st_onward_to_thule'
    label = 'Onward to Thule'
    description = "Thule, the fabled northern-most land! This is like my previous Northern Journey and Northern Warbler shaders combined. It's also somewhat more colorful."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
