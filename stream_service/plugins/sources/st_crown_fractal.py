"""Crown Fractal: Played around with elliptic curves, plugged in some complex numbers and played around some more. Calling it Crown Fractal because of the 3 to 6 jags on each of...

Source: https://www.shadertoy.com/view/4djcWm
Author: Fading
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * Permission is hereby granted, free of charge, to any person obtaining a copy
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/**
 * Copyright (c) 2017 Niklas Rosenstein
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

vec2 cmpxmul(in vec2 a, in vec2 b) {
	return vec2(a.x * b.x - a.y * b.y, a.y * b.x + a.x * b.y);
}

vec2 cmpxpow(in vec2 c, int p) {
	for (int i = 0; i < p; ++i) {
		c = cmpxmul(c, c);
	}
    return c;
}

int crown(in vec2 z, in vec2 c, int max) {
    int num = 0;
    while (length(z) < 2.0 && num < max) {
     	z = cmpxpow(z, 3) + z - cmpxpow(c, 2);
        num++;
    }
    return num;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    float t = iTime / 16.0;
    vec2 mp = vec2(cos(t), sin(t)) * 0.8;
	vec2 uv = fragCoord.xy / iResolution.xy * 2.0 - 1.0;
    int max = 12;
    int num = crown(mp, uv, max);
    float x = float(num) / float(max);
	fragColor = vec4(x, x, x, 1.0);
}
'''


class StCrownFractal(GlslSource):
    name = 'st_crown_fractal'
    label = 'Crown Fractal'
    description = 'Played around with elliptic curves, plugged in some complex numbers and played around some more. Calling it Crown Fractal because of the 3 to 6 jags on each of...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
