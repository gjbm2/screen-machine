"""Northern Melter: This changes the "Northern Elves" shader drastically by switching from sin and cos to Quilez' Basic Noise. It looks like melting liquid metal, which is fun.

Source: https://www.shadertoy.com/view/7stSzN
Author: TEttinger
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // CC0 licensed, do what thou wilt.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// CC0 licensed, do what thou wilt.

// change the seed to any not-too-huge float and the colors/shapes will change.
const float SEED = 69.42; // starts off with a "melting face"
const vec3 COEFFS = fract((SEED + 23.4567) * vec3(0.8191725133961645, 0.6710436067037893, 0.5497004779019703)) + 0.5;
const vec3 SECTION = fract(COEFFS.zxy - COEFFS.yzx * 1.618);

// Quilez Basic Noise, from https://www.shadertoy.com/view/3sd3Rs (MIT-license)
vec3 bas( vec3 x )
{
    // setup    
    vec3 i = floor(x);
    vec3 f = fract(x);
    vec3 s = sign(fract(x/2.0)-0.5);
    
    // use some hash to create a random value k in [0..1] from i
    vec3 k = fract(SECTION * i + i.yzx);

    // quartic polynomial
    return s*f*(f-1.0)*((16.0*k-4.0)*f*(f-1.0)-1.0);
}

// this is different from other swayRandomized in Northern demos because it uses Quilez basic noise instead of trigonometry.
vec3 swayRandomized(vec3 seed, vec3 value)
{
    return bas(seed.xyz + value.zxy - bas(seed.zxy + value.yzx) + bas(seed.yzx + value.xyz));
}

// this function, if given steadily-increasing values in con, may return exponentially-rapidly-changing results.
// even though it should always return a vec3 with components between -1 and 1, we use it carefully.
vec3 cosmic(vec3 c, vec3 con)
{
    return (con
    + swayRandomized(c, con)
    ) * 0.5;
    //+ swayRandomized(c + 1.1, con.xyz)
    //+ swayRandomized(c + 2.2, con.xyz)) * 0.25;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
//    vec2 uv = (fragCoord + cos(iTime * 0.3) * 64.0) * (2.125 + sin(iTime * 0.25));
    vec2 uv = (fragCoord * 0.1) + swayRandomized(COEFFS.zxy, (iTime * 0.1875) * COEFFS.yzx - fragCoord.yxy * 0.004).xy * 42.0;
    // aTime, s, and c could be uniforms in some engines.
    float aTime = iTime * 0.0625;
    vec3 adj = vec3(-1.11, 1.41, 1.61);
    vec3 s = (swayRandomized(vec3(34.0, 76.0, 59.0), aTime + adj)) * 0.25;
    vec3 c = (swayRandomized(vec3(27.0, 67.0, 45.0), aTime - adj.yzx)) * 0.25;
    vec3 con = vec3(0.0004375, 0.0005625, 0.0008125) * aTime + c * uv.x + s * uv.y;
    
    con = cosmic(COEFFS, con);
    con = cosmic(COEFFS + 1.618, con);
    
    fragColor = vec4(sin(con * 3.1416) * 0.5 + 0.5,1.0);
//    fragColor = vec4(swayRandomized(COEFFS + 3.0, con) * 0.5 + 0.5,1.0);
}'''


class StNorthernMelter(GlslSource):
    name = 'st_northern_melter'
    label = 'Northern Melter'
    description = 'This changes the "Northern Elves" shader drastically by switching from sin and cos to Quilez\' Basic Noise. It looks like melting liquid metal, which is fun.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
