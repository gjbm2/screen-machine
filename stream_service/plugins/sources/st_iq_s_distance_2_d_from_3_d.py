"""Iq's distance 2 D from 3 D: Useful for checking unnoticeable errors in 3D SDF's without raymarching. Individual frames are slices of planes from x and y facing z.

Source: https://www.shadertoy.com/view/7llSWS
Author: wildniklin
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// https://www.iquilezles.org/www/index.htm
// https://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm

const vec3 offset = vec3(0.0);
const vec3 scale = vec3(1.0);
const float scanSpeed = 0.125;
const float epsilon = 0.01;

float sdCircle(vec3 p, float r) {
    return length(p) - r;
}

float sd(vec3 p) {
    return sdCircle(p, 0.5);
}

vec3 sdColor(float d) {
    return mix((1.0 - (sign(d) * vec3(0.1, 0.4, 0.7))) * (1.0 - exp(-3.0 * abs(d))) * (0.8 + (0.2 * cos(150.0 * d))), vec3(1.0), 1.0 - smoothstep(0.0, 0.01, abs(d)));
}

vec3 normal(vec3 p) {
    vec2 e = vec2(0.0, epsilon);
    return normalize(vec3(
        sd(p + e.yxx) - sd(p - e.yxx),
        sd(p + e.xyx) - sd(p - e.xyx),
        sd(p + e.xxy) - sd(p - e.xxy)
    ));
}
vec2 closestPointLine(vec2 a, vec2 b, vec2 p) {
    vec2 ab = b - a;
    float dist = clamp(dot(p - a, ab) / dot(ab, ab), 0.0, 1.0);
    if(dist <= 0.0) return a;
    if(dist >= 1.0) return b;
    return mix(a, b, dist);
}

void mainImage(out vec4 o, vec2 i) {
    float tp = 1.0 / min(iResolution.x, iResolution.y);
    
	vec2 vp = ((i * 2.0) - iResolution.xy) * tp;
    vec2 vm = ((iMouse.xy * 2.0) - iResolution.xy) * tp;

    float vz = ((fract(iTime * scanSpeed) * 2.0) - 1.0);
    float z = vz * scale.z;
    
    vec3 p = vec3(vp * scale.xy, z) + offset;

	float d = sd(p);
    
    vec3 col = sdColor(d);

    if(iMouse.z > 0.0) {
        vec3 m = vec3(vm * scale.xy, z) + offset;
        float md = sd(m);
        vec3 n = normal(p);
        vec3 c = (n * 0.5) + 0.5;
        col = mix(c, col, smoothstep(-tp, tp, abs(length(p - m) - abs(md)) - tp));
        col = mix(c, col, smoothstep(-tp, tp, abs(length(p.xy - closestPointLine(m.xy, m.xy - n.xy * md, p.xy))) - tp));
    }
    
    if((vp.y < -0.97) && (vp.x < 1.0) && (vp.x > -1.0)) col = mix(col, step(vp.x, vz) * sdColor(vp.x * scale.z), 0.5);

	o = vec4(col, 1.0);
}'''


class StIqSDistance2DFrom3D(GlslSource):
    name = 'st_iq_s_distance_2_d_from_3_d'
    label = "Iq's distance 2 D from 3 D"
    description = "Useful for checking unnoticeable errors in 3D SDF's without raymarching. Individual frames are slices of planes from x and y facing z."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
