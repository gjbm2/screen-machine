"""Foam Noise: Foam Noise! Really this is just value noise, rotated to match a corner of the appropriate simplex, and domain-warping the next rotation, averaging all results...

Source: https://www.shadertoy.com/view/wssBz8
Author: TEttinger
License: BSD-3-Clause
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Reuse permitted under the BSD license.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// This Shadertoy ( https://www.shadertoy.com/view/wssBz8 ) shows "Foam Noise" by Tommy Ettinger.
// It's just value noise that's rotated and domain warps the next result.

// Based on a Shadertoy: https://www.shadertoy.com/view/4dS3Wd
// By Morgan McGuire @morgan3d, http://graphicscodex.com
// Reuse permitted under the BSD license.
// The improved value noise is also from a Shadertoy: https://www.shadertoy.com/view/MllBRr
// By afl_ext, public domain.

// For a single octave
#define NOISE foam

// For multiple octaves
//#define NOISE fbm
#define NUM_NOISE_OCTAVES 1

const highp float pi          = 3.1415926535;
const highp float pi2         = 2.0 * pi;
const highp float inf         = 1.0 / 0.0;
float square(float x) { return x * x; }
float infIfNegative(float x) { return (x >= 0.0) ? x : inf; }

const highp float PHI = 1.61803398874989484820459; // (phi = Golden Ratio)
const highp vec2 H2 = vec2(1.324717957244746, 1.754877666246693); // harmonious number for 2D, then its square
const highp vec3 H3 = vec3(1.8191725134, 1.6710436067, 1.5497004779); // harmonious 3D

float bounce(float x) {
    float y = fract(x);
    return mix(y, 1.0 - y, step(fract(x * 0.5), 0.5));
}

vec2 bounce(vec2 x) {
    vec2 y = fract(x);
    return mix(y, 1.0 - y, step(fract(x * 0.5), vec2(0.5)));
}

float hash(float seed, float p) {
    return bounce(fract((p - seed) * PHI + seed) * (PHI - p));
//    return fract(fract((p - seed) * PHI + seed) * (PHI - p));
//    return fract((cos(p * seed + PHI) - sin(p * PHI - seed)) * (31.0 + seed));
//    float f = floor(p);
//    float start = sin((cos(f * seed) + sin(f * 1024.)) * 345. + seed);
//    float end   = sin((cos((f+1.) * seed) + sin((f+1.) * 1024.)) * 345. + seed);
//    return mix(start, end, smoothstep(0., 1., p - f)) * 0.5 + 0.5;
}

    //////////WIP comments, feel free to ignore
    //// old, slow
//    return fract(dot(sin(seed + hm + p), cos(seed + hm * p)));
//    const float scale = -1.0 / log(2.125);
    //// thanks mgsx! https://www.mgsx.net/2015/07/21/006-011-fm-3D-WIP.html
//    return fract(sin(p * 12.9898 + seed) * 43758.5453 - p - seed);

    //    return acos(fract(sin(p * 12.9898 + seed) * 43758.5453 - p - seed) * 2.0 - 1.0) * (1.0 / pi);
//    return scale * log(fract(sin(p * 12.9898 + seed) * 43758.5453 - seed) + 1.0) + 1.0;
    //return pow(0.61803, 3.14159 * fract(sin((p+seed) * 12.9898) * 43758.5453 + (p+seed) * 2.6180339887498949));
//    return pow(0.61803, 3.14159 * fract(sin(p * 12.9898 + seed) * 43758.5453 - p - seed));


float hash(float seed, vec2 p) {
/*
    p = (fract((p - seed) * PHI + seed) * (PHI - p.yx));
    p = (fract((p.yx - seed) * PHI + seed) * (PHI - p));
    return bounce(p.x - p.y);
*/
    return fract(4768.1232345456 * sin((p.x+p.y*43.0+seed*137.0)));
	//return fract(4768.1232345456 * sin((p.x+p.y*43.0+p.z*137.0+p.w*2666.0)));
}
//    return fract((dot((p + seed), HAR) + seed) * (0.5 + fract(length(HAR.yx * p - seed))));

    //////////WIP comments, feel free to ignore
    //return fract(dot((seed * hm + p.xyx), (seed + hm * p.yxy)) * (0.5 - fract(length(p - seed))));
    //// faster way, seems better-distributed than the 1D noise, thanks again mgsx
//    return fract(sin(seed + dot(p, vec2(12.9898,78.233))) * 43758.5453 - length(p + seed));
//    return acos(fract(sin(seed + dot(p, vec2(12.9898,78.233))) * 43758.5453 - length(p + seed)) * 2.0 - 1.0) * (1.0 / pi);
    //return pow(0.61803, 3.14159 * fract(sin(seed + dot(p, vec2(12.9898,78.233))) * 43758.5453 - seed));
    //return pow(0.61803, 3.14159 * fract(sin(seed + dot(p, vec2(12.9898,78.233))) * 43758.5453 + length(p - seed)));

highp float noise(float seed, float x) {
    float i = floor(x);
    float f = fract(x);
    float u = f * f * (3.0 - 2.0 * f);
    return mix(hash(seed, i), hash(seed, i + 1.0), u);
}

highp float noise(float x) {
    return noise(42.0, x);
}
/*
float noise(float seed, vec2 p)
{    
    vec2 i = floor(p);
	vec2 f = fract(p);
    f = f*f*(3.0-2.0*f);
    return mix(mix(sin(pi*dot(p,g(seed,i+vec2(0.000000,0.000000)))),
               	   sin(pi*dot(p,g(seed,i+vec2(1.000000,0.000000)))),f.x),
               mix(sin(pi*dot(p,g(seed,i+vec2(0.000000,1.000000)))),
               	   sin(pi*dot(p,g(seed,i+vec2(1.000000,1.000000)))),f.x),f.y) * 0.5 + 0.5;
}
*/
highp float noise(float seed, vec2 x) {
/*    x.x -= seed;
    x.y += seed;
    x *= 0.125;
    vec2 y = bounce(x.yx + H2);
    y += (bounce(x * H2 + seed) * (PHI - y));
    //y -= (bounce(x * H2 - seed) * (PHI - y));
    return bounce(y.x - y.y + 0.5);
//    return sin(pi * (y.x - y.y)) * 0.5 + 0.5;
*/
/*
    vec2 y = sin(x * H2) * pi2;
    y += cos(y * H2 - x.yx);
    return sin(pi2 * ((y.x - y.y + 0.5) + (PHI + sin((x.y - x.x - 0.5) * pi2)))) * 0.5 + 0.5;
*/    
//    const highp vec2 step = vec2(43.0, 59.0);
    vec2 i = floor(x);
    vec2 f = fract(x);

	// Four corners in 2D of a tile
	float a = hash(seed, i);
    float b = hash(seed, i + vec2(1.0, 0.0));
    float c = hash(seed, i + vec2(0.0, 1.0));
    float d = hash(seed, i + vec2(1.0, 1.0));
/*
    float n = dot(i, step);

    float a = hash(seed, n);
    float b = hash(seed, n + dot(step, vec2(1.0, 0.0)));
    float c = hash(seed, n + dot(step, vec2(0.0, 1.0)));
    float d = hash(seed, n + dot(step, vec2(1.0, 1.0)));
*/
    vec2 u = f * f * (3.0 - 2.0 * f);
	return mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
}

highp float noise(vec2 x) {
    return noise(42.0, x);
}


highp float noise(float seed, vec3 x) {
    const highp vec3 step = vec3(59.0, 43.0, 37.0);

    vec3 i = floor(x);
    vec3 f = fract(x);
 
    // For performance, compute the base input to a 1D hash from the integer part of the argument and the 
    // incremental change to the 1D based on the 3D -> 1D wrapping
    float n = dot(i, step);

    vec3 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix( hash(seed, n                              ), hash(seed, n + dot(step, vec3(1., 0., 0.))), u.x),
                   mix( hash(seed, n + dot(step, vec3(0., 1., 0.))), hash(seed, n + dot(step, vec3(1., 1., 0.))), u.x), u.y),
               mix(mix( hash(seed, n + dot(step, vec3(0., 0., 1.))), hash(seed, n + dot(step, vec3(1., 0., 1.))), u.x),
                   mix( hash(seed, n + dot(step, vec3(0., 1., 1.))), hash(seed, n + dot(step, vec3(1., 1., 1.))), u.x), u.y), u.z);
}

highp float noise(vec3 x) {
    return noise(42.0, x);
}

highp float foam(float seed, float x) {
    return noise(seed, x);
}

highp float foam(float x) { return foam(61.0, x); }

highp float foam(float seed, vec2 x) {
    highp vec3 p = vec3(x.x, dot(x, vec2(-0.5, 0.8660254037844386)), dot(x, vec2(-0.5, -0.8660254037844386)));
    float a = noise(seed, p.yz);
    float b = noise(seed + 1.0, p.xz + a * 0.5);
    float c = noise(seed + 2.0, p.xy + b * 0.5);
    return smoothstep(0.0, 1.0, (a + b + c) * (1.0 / 3.0));
}

highp float foam(vec2 x) { return foam(61.0, x); }

highp float foam(float seed, vec3 x) {
    highp vec4 p = vec4(x.x, 
                  dot(x.xy, vec2(-0.3333333333333333, 0.9428090415820634)),
                  dot(x, vec3(-0.3333333333333333, -0.4714045207910317, 0.816496580927726)),
                  dot(x, vec3(-0.3333333333333333, -0.4714045207910317, -0.816496580927726)));
    float a = noise(seed, p.yzw);
    float b = noise(seed + 42.0, p.xzw + a * 0.5);
    float c = noise(seed + 84.0, p.xyw + b * 0.5);
    float d = noise(seed + 126.0, p.xyz + c * 0.5);
    return smoothstep(0.0, 1.0, 
           smoothstep(0.0, 1.0, (a + b + c + d) * 0.25)
           );
}

highp float foam(vec3 x) { return foam(61.0, x); }

float fbm(float x) {
	float v = 0.0;
	float a = 0.6666;
	float seed = 61.0;
	for (int i = 0; i < NUM_NOISE_OCTAVES; ++i) {
		v += a * foam(seed, x);
        seed += 42.0;
		x = x * 2.0 + seed;
		a *= 0.5;
	}
	return v;
}


float fbm(vec2 x) {
	float v = 0.0;
	float a = 0.6666;
	float seed = 61.0;
	// Rotate to reduce axial bias
    mat2 rot = mat2(cos(0.5), sin(0.5), -sin(0.5), cos(0.50));
	for (int i = 0; i < NUM_NOISE_OCTAVES; ++i) {
		v += a * foam(seed, x);
        seed += 42.0;
		x = rot * x * 2.0 + seed;
		a *= 0.5;
	}
	return v;
}


float fbm(vec3 x) {
	float v = 0.0;
	float a = 0.6666;
	float seed = 61.0;
	for (int i = 0; i < NUM_NOISE_OCTAVES; ++i) {
		v += a * foam(seed, x);
        seed += 42.0;
		x = x * 2.0 + seed;
		a *= 0.5;
	}
	return v;
}


//////////////////////////////////////////////////////////////////////////////////////
// Visualization:

// C = sphere center, r = sphere radius, P = ray origin, w = ray direction
float  intersectSphere(vec3 C, float r, vec3 P, vec3 w) {	
	vec3 v = P - C;
	float b = -dot(w, v);
	float c = dot(v, v) - square(r);
	float d = (square(b) - c);
	if (d < 0.0) { return inf; }	
	float dsqrt = sqrt(d);
	
	// Choose the first positive intersection
	return min(infIfNegative((b - dsqrt)), infIfNegative((b + dsqrt)));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
	highp float v = 0.0;
	
	// Visualize 1D, 2D, and 3D
	if (fragCoord.y > iResolution.y / 2.0) {
		if (fragCoord.x < iResolution.x / 2.0) {
			// 1D
		    float coord = fragCoord.x * 0.05 + iTime * 5.0 - 10.0;
			float height = NOISE(coord) * iResolution.y / 2.0;
			v = clamp((height - fragCoord.y + iResolution.y / 2.0) / (iResolution.y * 0.02), 0.0, 1.0);
		} else if (fragCoord.x < iResolution.x / 2.0 + 1.0) {
			fragColor.rgb = vec3(1.0);
			return;
		} else {
			// 2D
			vec2 coord = fragCoord.xy * 0.125 - vec2(iTime * 5.0, iResolution.y * 0.5);
			v = NOISE(coord);
		}
	} else if (fragCoord.y > iResolution.y / 2.0 - 1.0) {
		fragColor.rgb = vec3(1.0);
		return;
	} else {
		// Ray-sphere
		const float verticalFieldOfView = 25.0 * pi / 180.0;
			
	    vec3 P = vec3(sin(iTime) * 2.0, 0, 5.0);
		vec3 w = normalize(vec3(fragCoord.xy - iResolution.xy / vec2(2.0, 4.0), 
								(iResolution.y / 2.0) / (-2.0 * tan(verticalFieldOfView / 2.0))));

		float t = min(intersectSphere(vec3(0, 0, 0), 1.0, P, w),
					  intersectSphere(vec3(0, -2000, 0), 2000.0 - 1.0, P, w));
		if (t < inf) {
			vec3 X = P + w * t;
			// Fake lighting to make surfaces appear nicely
			v = NOISE(X * 10.0) * clamp(X.y * 0.75 + 1.0 - min(X.z * 0.05, 0.0), 0.0, 1.0) + 
				clamp((length(X.xz) - 0.75) * 0.15, 0.0, 0.1);
		} else {
			// Background
			v = 0.5;
		}
	}

    fragColor.rgb = vec3(v);
//    fragColor.rgb = vec3(pow(v, 0.375));
//    fragColor.rg += foam(1999.0, fragCoord.xy * 0.25) * 0.0625;
//    fragColor.g += foam(97.0, fragCoord.xy * 0.375) * 0.0625;
}'''


class StFoamNoise(GlslSource):
    name = 'st_foam_noise'
    label = 'Foam Noise'
    description = 'Foam Noise! Really this is just value noise, rotated to match a corner of the appropriate simplex, and domain-warping the next rotation, averaging all results...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
