"""Airplane   Modelling Exercise: Probably my best SDF model yet! I am planning on using it in a game or something else related.

Source: https://www.shadertoy.com/view/3lcyWX
Author: oneshade
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
Probably my best SDF model yet!
If you have suggestions on how to improve the propeller SDF I would really like to
know. In case you are wondering it is a squashed Bernoulli lemniscate.

The MIT License
Copyright © 2020 Elijah Bevers
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions: The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR
ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
*/

// Distance functions and SDF operators are from Inigo Quilez's distance functions article (https://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm):
float opExtrusion(in vec3 p, in float d, in float h) {
    // d is the distance to the 2D shape using the x and y components of p
    vec2 w = vec2(d, abs(p.z) - h);
    return min(max(w.x, w.y), 0.0) + length(max(w, 0.0));
}

vec3 opTwist(in vec3 p, in float t) {
    float c = cos(t * p.y);
    float s = sin(t * p.y);
    mat2 m = mat2(c, -s, s, c);
    return vec3(m * p.xz, p.y);
}

float sdBox2D(in vec2 p, in vec2 b) {
    vec2 q = abs(p) - b;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0);
}

float sdTrapezoid(in vec2 p, in float r1, in float r2, in float he) {
    vec2 k1 = vec2(r2, he);
    vec2 k2 = vec2(r2 - r1, 2.0 * he);

	p.x = abs(p.x);
    vec2 ca = vec2(max(0.0, p.x - ((p.y < 0.0) ? r1 : r2)), abs(p.y) - he);
    vec2 cb = p - k1 + k2 * clamp(dot(k1 - p, k2) / dot(k2, k2), 0.0, 1.0);
    
    float s = (cb.x < 0.0 && ca.y < 0.0) ? -1.0 : 1.0;
    
    return s * sqrt(min(dot(ca, ca), dot(cb, cb)));
}

float sdBox(in vec3 p, in vec3 b) {
    vec3 q = abs(p) - b;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}

float sdCylinder(in vec3 p, in float h, in float r) {
    vec2 d = abs(vec2(length(p.xz), p.y)) - vec2(r, h);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

float BernoulliDE(in vec2 p, in vec2 scale) {
    vec2 ps = p / scale;

    float l = ps.x - 1.0;
    float r = ps.x + 1.0;
    float ys = ps.y * ps.y;

    float bernoulli = sqrt(l * l + ys) * sqrt(r * r + ys) - 1.0;

    vec2 pSqrS = p / (scale * scale);
    float sInvX = 1.0 / scale.x;
    float ls = pSqrS.x - sInvX;
    float rs = pSqrS.x + sInvX;

    float psSqrY = ps.y * ps.y;
    float ld = sqrt(l * l + psSqrY);
    float rd = sqrt(r * r + psSqrY);

    float pdx = (ls * rd) / ld + (rs * ld) / rd;
    float pdy = (rd / ld + ld / rd) * pSqrS.y;
    float bernoulliGrad = sqrt(pdx * pdx + pdy * pdy);

    return bernoulli / bernoulliGrad;
}

float mapScene(in vec3 p) {
    float airplane = sdBox(p, vec3(0.4, 0.4, 2.8)) - 0.146;
    airplane += smoothstep(-4.0, 10.0, p.z);

    vec3 wp = p;
    wp.yz *= mat2(0.99801615628, 0.06295833376, -0.06295833376, 0.99801615628);

    float wing = sdTrapezoid(p.xz + vec2(0.0, 1.4), 2.85, 2.7, 0.3) - 0.25;
    airplane = min(airplane, opExtrusion(wp.xzy - vec3(0.0, 0.0, 0.212), wing, 0.052) - 0.1);

    vec3 tp = p - vec3(0.0, 0.0, 2.5);
    float tail = sdTrapezoid(tp.xz, 0.85, 1.0, 0.475) - 0.1;
    airplane = min(airplane, opExtrusion(tp.xzy, tail, 0.05) - 0.1);

    tp.yz -= vec2(1.0, 0.25 * tp.y - 0.025);
    tail = sdBox2D(tp.zy, vec2(0.3, 0.75)) - 0.1;
    tail = 0.82 * tail + tp.y * 0.164;
    airplane = min(airplane, opExtrusion(tp.zyx, tail, 0.05) - 0.1);

    vec3 pp = p + vec3(0.0, 0.0, 3.0);

    float r = iTime * 5.0;
    float c = cos(r), s = sin(r);
    pp.xy *= mat2(c, s, -s, c);

    airplane = min(airplane, sdCylinder(pp.xzy, 0.05, 0.2));

    float prop;
    if (abs(pp.x) > 1.5 || abs(pp.y) > 1.05 || abs(pp.z) > 0.25) {
        prop = sdBox(pp, vec3(1.4, 0.95, 0.2));
    }

    else {
        pp = opTwist(pp, 0.4).xzy;
        prop = opExtrusion(pp, BernoulliDE(pp.xy, vec2(1.0, 0.5)), 0.02) - 0.02;
        prop *= 0.9;
    }

    airplane = min(airplane, prop);

    vec3 sp = vec3(abs(wp.x) - 0.51, p.yz + vec2(1.0, 2.5));
    sp.xy *= mat2(0.98877107793, 0.14943813247, -0.14943813247, 0.98877107793);

    float struts = sdTrapezoid(sp.zy, 0.1, 0.2, 0.55) - 0.05;
    airplane = min(airplane, opExtrusion(sp.yzx, struts, 0.03) - 0.025);

    wp = sp + vec3(0.0, 0.5, 0.0);
    float wheels = sdCylinder(wp.yxz, 0.1, 0.24) - 0.05;
    airplane = min(airplane, wheels);

    return airplane;
}

vec3 getNormal(in vec3 p) {
    return normalize(vec3(mapScene(p + vec3(0.001, 0.0, 0.0)) - mapScene(p - vec3(0.001, 0.0, 0.0)),
                          mapScene(p + vec3(0.0, 0.001, 0.0)) - mapScene(p - vec3(0.0, 0.001, 0.0)),
                          mapScene(p + vec3(0.0, 0.0, 0.001)) - mapScene(p - vec3(0.0, 0.0, 0.001))));
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = (fragCoord.xy - 0.5 * iResolution.xy) / iResolution.y;
    fragColor = vec4(0.0, 0.0, 0.0, 1.0);

    vec3 ro = vec3(0.0, 0.0, 10.0);
    vec3 rd = normalize(vec3(uv, -1.0));

    float c1 = cos(iTime), s1 = sin(iTime);
    float c2 = cos(0.25), s2 = sin(0.25);

    float t = 0.0;
    for (float iters=0.0; iters < 150.0; iters++) {
        vec3 p = ro + rd * t;

        p.y += 0.5;
        p.xz *= mat2(c1, -s1, s1, c1);
        p.yz *= mat2(c2, s2, -s2, c2);

        float d = mapScene(p / 1.5) * 1.5;
        if (d < 0.001) {
            vec3 n = getNormal(p / 1.5) * 1.2;
            vec3 l = vec3(-0.58, 0.58, 0.58);

            n.yz *= mat2(c2, -s2, s2, c2);
            n.xz *= mat2(c1, s1, -s1, c1);

            fragColor.rg += max(0.2, dot(n, l));
            break;
        }

        if (t > 50.0) {
            break;
        }

        t += d;
    }
}'''


class StAirplaneModellingExercise(GlslSource):
    name = 'st_airplane_modelling_exercise'
    label = 'Airplane   Modelling Exercise'
    description = 'Probably my best SDF model yet! I am planning on using it in a game or something else related.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.45)
