"""Northern Stream, Maybe Clean: There's an awful lot of bright colors for a river, but it does look like dyes flowing through water somewhat. Part of my Northern Lights series, with some...

Source: https://www.shadertoy.com/view/sdXSRM
Author: TEttinger
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // CC0 licensed, do what thou wilt.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// CC0 licensed, do what thou wilt.
const float SEED = 42.0;

// UE4 PseudoRandom function
float pseudo(vec2 v) {
    v = v + vec2(-64.340622, -72.465622);
    return sin(dot(v.xyx * v.xyy, vec3(20.390625, 60.703125, 2.4281209)));
}

float swayRandomized(float seed, float value)
{
    float f = floor(value);
    float start = pseudo(vec2(seed, f));
    float end   = pseudo(vec2(seed, f+1.0));
    return mix(start, end, smoothstep(0., 1., value - f));
}

vec3 cosmic(float seed, vec3 con)
{
    con.x += swayRandomized(seed, con.z + con.x);
    con.y += swayRandomized(seed, con.x + con.y);
    con.z += swayRandomized(seed, con.y + con.z);
    return con * 0.5;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = 8.0 * fragCoord/iResolution.xy;
    // aTime, s, and c could be uniforms in some engines.
    float aTime = iTime * 0.225;
    vec3 s = vec3(swayRandomized(-164.0531527, aTime - 1.11),
                  swayRandomized(-776.648142, aTime + 1.41),
                  swayRandomized(-509.935190, aTime + 1.61));
    vec3 c = vec3(swayRandomized(-105.2792407, aTime - 1.11),
                  swayRandomized(-615.576687, aTime + 1.41),
                  swayRandomized(-435.278990, aTime + 1.61));
    vec3 con = vec3(0.0004375, 0.0005625, 0.0008125) * aTime + c * uv.x + s * uv.y;
    con = cosmic(SEED, con);
    con = cosmic(SEED, con);
    con = cosmic(SEED, con);
    
    fragColor = vec4(sin(con * 3.14159265) * 0.5 + 0.5,1.0);
}'''


class StNorthernStreamMaybeClean(GlslSource):
    name = 'st_northern_stream_maybe_clean'
    label = 'Northern Stream, Maybe Clean'
    description = "There's an awful lot of bright colors for a river, but it does look like dyes flowing through water somewhat. Part of my Northern Lights series, with some..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
