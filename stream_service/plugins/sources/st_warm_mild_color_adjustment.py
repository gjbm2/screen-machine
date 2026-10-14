"""Warm/Mild Color Adjustment: This is a simple example of a shader that edits colors in the YCwCm color space (see block comment at top)

Source: https://www.shadertoy.com/view/ttf3D4
Author: TEttinger
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // CC0 license; any and all rights to this work are waived by the author.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// CC0 license; any and all rights to this work are waived by the author.
/* These are some simple color-tweaking effects using a variant on YCbCr and YCoCg that I hacked
 * together, called YCwCm.
 * Y is luma (perceptual lightness) and it isn't calculated especially well; it ranges from 0.0 
 * to 1.0, with 0.0 including but not limited to black, and 1.0 including (not just) white.
 * Cw is Chroma Warm, and determines whether a color is close to blue/green or red/yellow; it
 * ranges from -1.0 to 1.0, with -1.0 blue to green, 0.0 purple to grayscale to chartreuse, and 1.0
 * red to yellow. Cm is Chroma Mild, and determines whether a color is close to blue/red or
 * green/yellow; it ranges from -1.0 to 1.0, with -1.0 blue to red, 0.0 cyan to gray to orange, and
 * 1.0 green to yellow. 
 * You can change mul and add to edit the effect. A few versions are provided.
 */

//// yellowing dingy effect
//const vec3 mul = vec3(0.9, 0.7, 0.75);
//const vec3 add = vec3(0.05, 0.14, 0.16);

//// hot/on-fire effect?
//const vec3 mul = vec3(1.1, 0.8, 1.0);
//const vec3 add = vec3(-0.1, 0.65, 0.2);

// ice effect?
const vec3 mul = vec3(1.0, 0.7, 0.7);
const vec3 add = vec3(0.4, -0.5, -0.15);

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 mulWave = mix(mul, vec3(1.0), sin(iTime) * 0.5 + 0.5);
    vec3 addWave = mix(add, vec3(0.0), sin(iTime) * 0.5 + 0.5);

    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord/iResolution.xy;

    vec4 tgt = texture( iChannel0, uv );
    // xyz is used when a color is using luma, warm, mild instead of red, green, blue channels
    tgt.xyz = addWave + mulWave * vec3(dot(tgt.rgb, vec3(0.375, 0.5, 0.125)), tgt.r - tgt.b, tgt.g - tgt.b);
    fragColor.rgb = clamp(vec3(
           dot(tgt.xyz, vec3(1.0, 0.625, -0.5)),
           dot(tgt.xyz, vec3(1.0, -0.375, 0.5)),
           dot(tgt.xyz, vec3(1.0, -0.375, -0.5))),
       0.0, 1.0);
    fragColor.a = 1.0;
}
'''


class StWarmMildColorAdjustment(GlslSource):
    name = 'st_warm_mild_color_adjustment'
    label = 'Warm/Mild Color Adjustment'
    description = 'This is a simple example of a shader that edits colors in the YCwCm color space (see block comment at top)'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.5)
