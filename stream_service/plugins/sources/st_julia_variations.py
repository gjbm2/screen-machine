"""Julia variations: Julia fractal variant, where the iterative function zᵢ₊₁=zᵢⁿ+c has a time-varying value of n, and mouse clicks determine the c-value.

Source: https://www.shadertoy.com/view/MlGczD
Author: BenWheatley
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * License MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
 * "Julia variations" by Ben Wheatley - 2018
 * License MIT License
 * Contact: github.com/BenWheatley
 */

const int LOOP_MAX = 64;
const float PI = 3.14159265359;

float complexAbs2(vec2 z) {
    return (z.x*z.x)+(z.y*z.y);
}

float arg(vec2 z) {
	float x = z.x;
	float y = z.y;
    if (x>0.0) {
		return atan(y/x);
    } else if (y>0.0) {
		return atan(y/x)+PI;
    }
	return atan(y/x)-PI;
}

vec2 complexExponent(vec2 z, float power) {
    float r = sqrt(complexAbs2(z));
    float arg = arg(z);
    
    float log_z_real = log(r);
    float log_z_imag = arg;
    
    float x_log_real = power * log_z_real;
    float x_log_imag = power * log_z_imag;
    
    float result_r = exp(x_log_real);
    
    return result_r*vec2(cos(x_log_imag), sin(x_log_imag));
}

vec3 rgbFromHue(float hue){
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(hue + K.xyz) * 6.0 - K.www);
    return 0.75 * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), 0.75);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord/iResolution.y;
	
    vec2 offset1 = vec2(1.0, 0.5);
    float scale = 2.0;
    
    vec2 c = vec2(iMouse.x/iResolution.x, iMouse.y/iResolution.y);
    c -= vec2(0.5, 0.5);
    c *= 2.0;
    vec2 z = (uv - offset1)*scale;
    
    vec3 rgb = vec3(0.0, 0.0, 0.0);
    float basic = 0.0;
    
    for (int i=0; i<LOOP_MAX; i++) {
        z = complexExponent(z, 3.0+(2.0*sin(iTime/2.0))) + c;
        if (complexAbs2(z)>4.0) {
            basic = float(i)/float(LOOP_MAX);
			rgb = rgbFromHue(basic);
            i = LOOP_MAX;
            
        } else if (i==(LOOP_MAX-1)) {
            rgb = vec3(0, 0, 0);
        }
    }
    
    // Output to screen
    fragColor = vec4(rgb, 1.0);
}'''


class StJuliaVariations(GlslSource):
    name = 'st_julia_variations'
    label = 'Julia variations'
    description = 'Julia fractal variant, where the iterative function zᵢ₊₁=zᵢⁿ+c has a time-varying value of n, and mouse clicks determine the c-value.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
