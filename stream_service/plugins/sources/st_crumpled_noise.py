"""Crumpled Noise: Lots of wrinkly blocky blobs of color, adjusted in YCbCr color space. Useful for making your brain try to form visual patterns from random noise, like for...

Source: https://www.shadertoy.com/view/ltcyDj
Author: TEttinger
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // CC0 license, do what thou wilt
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''
// CC0 license, do what thou wilt

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // single float input, we get a varied value for it from screen position and time


    float rate = 20.0; //fps
    float size = 7.3;
    float jitter = 6.87; // when larger, distorts squares more
    float gamma = 1.1; // not sRGB gamma; arbitrary
    
    float time = floor(iTime * rate) / 60.0;
    float dx = jitter * fract(exp(fract(time * 53.678921) + 0.234567));
    float dy = jitter * fract(exp(fract(time * 31.456789) + 0.345672));	
    
    
    vec2 xy = floor((fragCoord) / size);
	vec3 uv = mod((vec3((xy / iResolution.xy), time) * 11035.15245)
                  // these are based off a number I had good results with as an increment with
                  // 64-bit integer CPU hashing, 0x369DEA0F31A53F85, and its powers modulo 64 bits
                  + vec3(393555.9000370003845, -55865.4634555294951, 782941.5577042901757),
                  96.9875);
    uv = mod((vec3((floor(vec2(fragCoord.x + fract(dot(uv.zyx + uv.zxx + uv.yzy, fract(uv * uv)))
                           * jitter - jitter,
                               fragCoord.y + fract(dot(uv + uv.yyx + uv.zxz, fract(uv.zxy * uv.zxy)))
                              * jitter - jitter) / size) / iResolution.xy), time) * 11035.15245)
             + vec3(393555.9000370003845, -55865.4634555294951, 782941.5577042901757),
             96.9875);
    float h = fract(dot(uv + uv.yzx + uv.zxy, fract(uv.zyx * uv.zyx)));
    float Y = h;
    float Cr = fract(h * 78294.15577042901757) - .5;
    float Cb = fract(h * 258.654634555294951) - .5;
    
    float R = Y + 1.402 * Cr;
	float G = Y - 0.344136 * Cb - 0.714136 * Cr;
	float B = Y + 1.772 * Cb;
    
    vec3 RGB = pow(vec3(R,G,B), vec3(gamma));
    
    fragColor = vec4(RGB, 1.0);
}'''


class StCrumpledNoise(GlslSource):
    name = 'st_crumpled_noise'
    label = 'Crumpled Noise'
    description = 'Lots of wrinkly blocky blobs of color, adjusted in YCbCr color space. Useful for making your brain try to form visual patterns from random noise, like for...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
