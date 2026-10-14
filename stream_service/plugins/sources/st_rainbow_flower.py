"""Rainbow flower: Messing around with trigonometric functions

Source: https://www.shadertoy.com/view/4djSWm
Author: bauble
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License: CC0 1.0
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// License: CC0 1.0
// http://creativecommons.org/publicdomain/zero/1.0/

const int iterations = 8;
const float colour_separation = 6.283185307/float(iterations);
float angle_separation = colour_separation * (sin(iTime * 0.002)* 16.0 + 16.0);

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = vec2(fragCoord.xy / iResolution.xy) * 2.0 - 1.0;
    uv.x *= iResolution.x / iResolution.y;
    float d = dot(uv, uv);
    float a = atan(uv.x, uv.y);
    vec3 c = vec3(0);
    for (int i = 0; i < 16; ++i)
    {
    	float x = (pow(d, 0.02) * 2.5 - 2.3 + sin((a + float(i) * angle_separation) *5.0) * 0.1) * 10.0;
    	x = x < 1.0 ? x : 2.0 - x;
        vec3 r = vec3(sin(float(i) * colour_separation        ) * 0.5 + 0.5,
                      sin(float(i) * colour_separation + 2.094) * 0.5 + 0.5,
                      sin(float(i) * colour_separation + 4.189) * 0.5 + 0.5);
        c += r*x;
    }
    c = c / float(iterations) * 2.0;
    vec3 f = pow(c, vec3(0.8));
    f *= clamp(c * 2.0, vec3(0.25), vec3(1.0));
	fragColor = vec4(f, 1.0);
}'''


class StRainbowFlower(GlslSource):
    name = 'st_rainbow_flower'
    label = 'Rainbow flower'
    description = 'Messing around with trigonometric functions'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
