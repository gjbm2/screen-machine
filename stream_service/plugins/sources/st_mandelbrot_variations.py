"""Mandelbrot variations: Mandelbrot variations where the iterative function is replaced with zᵢ₊₁=zᵢⁿ+c for 1.0<c<5.0. Also animated to be a 1960s screensaver.

Source: https://www.shadertoy.com/view/4scyzl
Author: BenWheatley
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * License MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
 * "Mandelbrot variations" by Ben Wheatley - 2018
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
	
    float theta = sin(iTime/2.2);
    vec2 uv_rotated = vec2(uv.x*cos(theta)-uv.y*sin(theta), uv.x*sin(theta)+uv.y*cos(theta));
    uv = uv_rotated;
    
    vec2 offset1 = vec2(0.9, 0.5);
    vec2 offset2 = vec2(0.3*sin(PI/2.0+iTime/7.0), 0.5*sin(-iTime/5.0));
    float scale = 1.0+0.75*sin(iTime/2.0);
    
    vec2 c = (uv - offset1 - offset2)*scale;
    vec2 z = c;
    
    vec3 rgb = vec3(0.0, 0.0, 0.0);
    float basic = 0.0;
    
    for (int i=0; i<LOOP_MAX; i++) {
        z = complexExponent(z, 3.0+(2.0*sin(iTime/2.0))) + c;
        if (complexAbs2(z)>4.0) {
            basic = float(i)/float(LOOP_MAX);
			rgb = rgbFromHue(basic+iTime/2.0);
            i = LOOP_MAX;
            
        } else if (i==(LOOP_MAX-1)) {
            rgb = vec3(sin(z.x+iTime),
            -sin(z.y+iTime),
            cos(z.x*z.y + iTime));
        }
    }
    
    // Output to screen
    fragColor = vec4(rgb, 1.0);
}'''


class StMandelbrotVariations(GlslSource):
    name = 'st_mandelbrot_variations'
    label = 'Mandelbrot variations'
    description = 'Mandelbrot variations where the iterative function is replaced with zᵢ₊₁=zᵢⁿ+c for 1.0<c<5.0. Also animated to be a 1960s screensaver.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
