"""Manhattan: Voronoi shading using Manhattan distance; click rhs for symmetry, lhs to turn off symmetry.

Source: https://www.shadertoy.com/view/4ldBzM
Author: teraspora
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Permission is hereby granted, free of charge, to any person obtaining a copy
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Manhattan - a fragment shader in GLSL, built on shadertoy.com;
// Author: John Lynch (@teraspora);
// Based on voronoi-2
// Date: 9 OCT 2018.

// Copyright © 2018 John Lynch

// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:

// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.

// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
// -----------------------------------------------------------------------------

#define PI 3.141592653588

// Some useful colours:
const vec3 white =      vec3(1.,   1.,   1.  );
const vec3 crimson =      vec3(1.,   0.,   0.4  );
const vec3 yellow =      vec3(1.,   1.,   0.  );
const vec3 black =      vec3(0.,   0.,   0.  );
const vec3 orange = vec3(1.0, 0.4, 0.);
const vec3 cyan = vec3(0., 0.4, 1.0);
const vec3 magenta = vec3(1.0, 0., 1.0);
const vec3 gold = vec3(1.0, 0.84, 0.66);

vec3[] cols = vec3[](white, crimson, yellow, magenta, gold, cyan, orange);
vec3 col;

// ==================================================================
// Some functions adapted from Github - 
// https://github.com/tobspr/GLSL-Color-Spaces/blob/master/ColorSpaces.inc.glsl
// - not tested!

vec3 hue2rgb(float hue)
{
    float R = abs(hue * 6. - 3.) - 1.;
    float G = 2. - abs(hue * 6. - 2.);
    float B = 2. - abs(hue * 6. - 4.);
    return clamp(vec3(R,G,B), 0., 1.);
}

// Converts a value from linear RGB to HCV (Hue, Chroma, Value)
vec3 rgb2hcv(vec3 rgb) {
    // Based on work by Sam Hocevar and Emil Persson
    vec4 P = (rgb.g < rgb.b) ? vec4(rgb.bg, -1.0, 2.0/3.0) : vec4(rgb.gb, 0.0, -1.0/3.0);
    vec4 Q = (rgb.r < P.x) ? vec4(P.xyw, rgb.r) : vec4(rgb.r, P.yzx);
    float C = Q.x - min(Q.w, Q.y);
    float H = abs((Q.w - Q.y) / (6. * C + 1.e-10) + Q.z);
    return vec3(H, C, Q.x);
}

// Converts from HSL to linear RGB
vec3 hsl2rgb(vec3 hsl) {
    vec3 rgb = hue2rgb(hsl.x);
    float C = (1. - abs(2. * hsl.z - 1.)) * hsl.y;
    return (rgb - 0.5) * C + hsl.z;
}

// Converts from linear rgb to HSL
vec3 rgb2hsl(vec3 rgb) {
    vec3 HCV = rgb2hcv(rgb);
    float L = HCV.z - HCV.y * 0.5;
    float S = HCV.y / (1. - abs(L * 2. - 1.) + 1.e-10);
    return vec3(HCV.x, S, L);
}

// My inline library of useful functions:
// =======================================

float arg(vec2 z) {
    return atan(z.y, z.x);
}

vec2 polar(float r, float phi) {
    return vec2(r * cos(phi), r * sin(phi));
}

vec2 times(vec2 v, vec2 w) {
    return vec2(v.x * w.x - v.y * w.y, v.x * w.y + v.y * w.x);
}

vec2 rotate(vec2 v, float phi) {
    return times(v, polar(1.0, phi)) ;
}

float om(float x) {
	return 1. - x;
}

vec3 om(vec3 v) {
	return 1. - v;
}

float op(float x) {
	return 1. + x;
}

float nsin(float x) {
	return op(sin(x)) * 0.5;
}

float ncos(float x) {
	return op(cos(x)) * 0.5;
}

float sqrtp(float x) {
	return sqrt(x < 0. ? -x : x); 
}

float nmmod(float x, float y) {
	float xmn = 2. * mod(x, y) / y;
    return xmn < 1. ? xmn : om(xmn);
}

vec3 omss(float mn, float mx, vec3 val) {
	return om(smoothstep(mn, mx, val));
}

vec2 nmouse() {
	return iMouse.xy / iResolution.xy;
}

vec3 invert(vec3 col) {
 	return 1. - clamp(col, 0., 1.);   
}

bool isOdd(float p) {
    return fract(p * 0.5) >= 0.5;
}

float minkd(vec2 u, vec2 v, float order) {	// Minkowski distance order 1
    if (order <= 0.) return 0.;				// i.e. Manhattan distance
	return abs(pow(abs(pow(v.x - u.x, order)) + abs(pow(v.y - u.y, order)), 1. / order)); 
}


// =======================================

// rand generator from 
// https://www.youtube.com/watch?v=l-07BXzNdPw&t=740s
vec2 r22(vec2 p) {
	vec3 a = fract(p.xyx * vec3(923.34, 234.34, 345.65));
	a += dot(a, a - 34.45);
    return fract(vec2(a.x * a.y, a.y * a.z));
}


float f(float x) {
	return sin(cos(0.2 * iTime * x)) + cos(2. * sin(0.23 * iTime * x)) - sqrt(0.2 *abs(x) * cos(0.02 * iTime * x));
}

vec3 drawBorder(vec3 col, float b, vec3 insetColour, vec2 pp, vec2 hr, float tileIndex) {
    // `b` is the border width
    // `col` is the input (and output) colour
    // `pp` is (unnormalised) coordinate of pixel
    // `hr` is (unnormalised) resolution
    // Make a border: `b` px solid black; with `insetColour` inset:
    if (tileIndex != -1.) {
    	//  any special code for single-pane use goes here    
    }
    // Make a line inset:
    if ((pp.x > b - 1. && pp.x <= b + 1.) || (pp.x > hr.x - b - 1. && pp.x < hr.x - b + 1.)) col = insetColour;
    if ((pp.y > b - 1. && pp.y <= b + 1.) || (pp.y > hr.y - b - 1. && pp.y < hr.y - b + 1.)) col = insetColour;
    
    // Now put a black border on top:
    col *= step(b, pp.x);
    col *= step(b, pp.y);
    col *= (1. - step(hr.x - b, pp.x));
    col *= (1. - step(hr.y - b, pp.y));
    return col;
    
    }

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
    float t = iTime / 4. + 2.;
    float numPoints = clamp(floor(t / 2.), 0., 64.);
    
    //t = 1. - t;
    float scale =  0.5; // / numPoints;
    
	float asp = iResolution.x/iResolution.y;
    
    // Normalized pixel coordinates (y from -1 to 1)
    vec2 uv = (2. * fragCoord - iResolution.xy) / (iResolution.y * scale);
		
    //uv.y = abs(uv.y);
    if (nmouse().x >= 0.5) uv = abs(uv);
    
    vec2 friz = vec2(100. * sin(t * uv.x), 100. * cos(t * uv.y));
    vec2 vel = vec2(1., 10.);
    
    //uv *= vec2(om(sin(friz.x * cos(vel.x * t) * uv.x) / 7.), om(cos(friz.y * sin(vel.y * t) * uv.y) / 11.)); 
    
    // col = crimson;
    
    float m = 0.;
    float mind = 100.;
    float ci;
    
    // generate a bunch of random points
    for (float i = 0.; i < numPoints; i++) {
    	vec2 n = r22(vec2(i));
        // sin of both components varied with time
        vec2 p = sin(n * t);
        // get distance to point
        float d = minkd(uv, p, 1.);
        //m += smoothstep(.05, .01, d);
        if (d < mind) {
        	mind = d;
            ci = i;
        }
    }
    
    col = invert(vec3(mind));
    col.g -= nsin(t + length(uv));
    //col.b += 0.5 * smoothstep(0., 1., col.r + col.b);
    
    if (rgb2hcv(col).z < 0.01) col = 0.5*cos(0.4 * t+uv.yxy / 6. +vec3(2.7,4.9,8.1));
    
    
    fragColor = vec4(col.bgr, 1.);
}

//col = vec3(m, nsin(length(uv)), ncos(t));
 '''


class StManhattan(GlslSource):
    name = 'st_manhattan'
    label = 'Manhattan'
    description = 'Voronoi shading using Manhattan distance; click rhs for symmetry, lhs to turn off symmetry.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
