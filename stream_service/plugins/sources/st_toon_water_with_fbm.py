"""Toon Water with FBM: https://thebookofshaders.com/13/

Source: https://www.shadertoy.com/view/4tffzN
Author: rafaelcastrocouto
License: BSD-3-Clause
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Reuse permitted under the BSD license.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// By Morgan McGuire @morgan3d, http://graphicscodex.com
// Reuse permitted under the BSD license.

// All noise functions are designed for values on integer scale.
// They are tuned to avoid visible periodicity for both positive and
// negative coordinates within a few orders of magnitude.

// For multiple octaves
#define NOISE fbm
#define NUM_NOISE_OCTAVES 2
#define SPEED 1.0
//#define SMOOTH 1


float hash(float n) { return fract(sin(n) * 1e4); }
float hash(vec2 p) { return  (sin(iTime*3.0*SPEED)*0.02) + fract(1e4 * sin(17.0 * p.x + p.y * 0.1) * (0.1 + abs(sin(p.y * 13.0 + p.x)))); }

float noise(float x) {
    float i = floor(x);
    float f = fract(x);
    float u = f * f * (3.0 - 2.0 * f);
    return mix(hash(i), hash(i + 1.0), u);
}


float noise(vec2 x) {
    vec2 i = floor(x);
    vec2 f = fract(x);

	// Four corners in 2D of a tile
	float a = hash(i);
    float b = hash(i + vec2(1.0, 0.0));
    float c = hash(i + vec2(0.0, 1.0));
    float d = hash(i + vec2(1.0, 1.0));

    // Simple 2D lerp using smoothstep envelope between the values.
	// return vec3(mix(mix(a, b, smoothstep(0.0, 1.0, f.x)),
	//			mix(c, d, smoothstep(0.0, 1.0, f.x)),
	//			smoothstep(0.0, 1.0, f.y)));

	// Same code, with the clamps in smoothstep and common subexpressions
	// optimized away.
    vec2 u = f * f * (3.0 - 2.0 * f);
	return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
}


float noise(vec3 x) {
    const vec3 step = vec3(110, 241, 171);

    vec3 i = floor(x);
    vec3 f = fract(x);
 
    // For performance, compute the base input to a 1D hash from the integer part of the argument and the 
    // incremental change to the 1D based on the 3D -> 1D wrapping
    float n = dot(i, step);

    vec3 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix( hash(n + dot(step, vec3(0, 0, 0))), hash(n + dot(step, vec3(1, 0, 0))), u.x),
                   mix( hash(n + dot(step, vec3(0, 1, 0))), hash(n + dot(step, vec3(1, 1, 0))), u.x), u.y),
               mix(mix( hash(n + dot(step, vec3(0, 0, 1))), hash(n + dot(step, vec3(1, 0, 1))), u.x),
                   mix( hash(n + dot(step, vec3(0, 1, 1))), hash(n + dot(step, vec3(1, 1, 1))), u.x), u.y), u.z);
}


float fbm(float x) {
	float v = 0.0;
	float a = 0.5;
	float shift = float(100);
	for (int i = 0; i < NUM_NOISE_OCTAVES; ++i) {
		v += a * noise(x);
		x = x * 2.0 + shift;
		a *= 0.5;
	}
	return v;
}


float fbm(vec2 x) {
	float v = 0.0;
	float a = 0.5;
	vec2 shift = vec2(100);
	// Rotate to reduce axial bias
    mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.50));
	for (int i = 0; i < NUM_NOISE_OCTAVES; ++i) {
		v += a * noise(x);
		x = rot * x * 2.0 + shift;
		a *= 0.5;
	}
	return v;
}


float fbm(vec3 x) {
	float v = 0.0;
	float a = 0.5;
	vec3 shift = vec3(100);
	for (int i = 0; i < NUM_NOISE_OCTAVES; ++i) {
		v += a * noise(x);
		x = x * 2.0 + shift;
		a *= 0.5;
	}
	return v;
}


void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
    vec2 coord = fragCoord.xy * 0.015 - vec2(iTime * 0.5, iResolution.y / 2.0);
    float speed = 0.3*SPEED;
    float limit = 0.1;
    float border = 0.025;
    float c = NOISE(coord - speed*iTime ) * NOISE(coord + speed*iTime );
    //vec3 color = vec3(c, c, c);
    vec3 color = vec3(step(limit-border,c), step(limit, c), 1);
    if (color.x == 1.0 && color.y != 1.0 && color.x == 1.0) { color = vec3(1.0, 1.0, 1.0); }
    else { color = vec3(0.06, 0.4, 1.0); }
#ifdef SMOOTH
    c = smoothstep(limit - border, limit, c) - smoothstep(limit, limit + border, c);
    fragColor = vec4(c * c * c, 0.25 + 0.75 * c * c, 0.5 + 0.5 * c, 1.0);
#else
    fragColor.rgb = clamp(color, 0.0, 1.0);
#endif
}'''


class StToonWaterWithFbm(GlslSource):
    name = 'st_toon_water_with_fbm'
    label = 'Toon Water with FBM'
    description = 'https://thebookofshaders.com/13/'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
