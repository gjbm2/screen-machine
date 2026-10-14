"""Brmlab logo: logo of prague hackerspace brmlab

Source: https://www.shadertoy.com/view/NlS3Wc
Author: lanikjo
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Permission is hereby granted, free of charge, to any person obtaining a copy
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//
// GLSL Brmlab logo
// Copyright (c) 2021 Jovan Lanik
//

// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.

float doCircle(vec2 uv, float x, float y, float size) {
    return floor(length(uv+vec2(x, y)) - size + 0.988);
}

float doRing(vec2 uv, float x, float y, float size, float thicc) {
    float ring = length(uv+vec2(x, y)) - size;
    ring = abs(ring) * thicc;
    ring = floor(ring);
    return ring;
}

float doBrm(vec2 uv, float x, float y, float size) {
    float ringSize = 0.425;
    float detailThicc = 80.0;

    uv += vec2(x, y);
    uv *= size;
    
    float ring = doRing(uv, 0.0, 0.0, ringSize, 40.0);
    
    float small = doRing(uv, 0.0, 0.0, ringSize - 0.1, detailThicc);
    
    small = max(small, 1.0 - doCircle(uv, 0.0, -0.33, 0.07));
    small = min(small, doRing(uv, 0.0, -0.33, 0.07, detailThicc));
    
    small = max(small, 1.0 - doCircle(uv, 0.25, 0.21, 0.07));
    small = min(small, doRing(uv, 0.25, 0.21, 0.07, detailThicc));
    
    small = max(small, 1.0 - doCircle(uv, -0.25, 0.21, 0.07));
    small = min(small, doRing(uv, -0.25, 0.21, 0.07, detailThicc));
    
    small = max(small, doCircle(uv, 0.0, 0.0, ringSize - 0.1));
    
    vec2 boxV = uv * 1.0;
    boxV = abs(boxV) * 1.0;
    boxV -= 0.04;
    boxV.x -= 0.04;
    boxV = abs(boxV) * 80.0;
    boxV = floor(boxV);
    boxV.x = max(boxV.x, floor(abs(uv.y)*19.0));
    boxV.y = max(boxV.y, floor(abs(uv.x)*12.0));
    float box = min(boxV.x, boxV.y);
 
    float put = min(ring, small);
    put = min(put, box);
    put = min(put, doRing(uv, 0.0, -0.16, 0.035, detailThicc));
    put = min(put, doRing(uv, 0.15, -0.16, 0.035, detailThicc));
    put = min(put, doRing(uv, -0.15, -0.16, 0.035, detailThicc));
    
    put = min(put, doRing(uv, 0.20, 0.05, 0.035, detailThicc));
    put = min(put, doRing(uv, -0.20, 0.05, 0.035, detailThicc));
    
    put = min(put, doRing(uv, 0.08, 0.20, 0.035, detailThicc));
    put = min(put, doRing(uv, -0.08, 0.20, 0.035, detailThicc));
    
    return put;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{  
    vec2 uv = fragCoord/iResolution.xy - 0.5;
    uv.x *= iResolution.x / iResolution.y;
    
    float var = (sin(iTime*20.0)+1.0)*0.01 + 1.0;
    
    float put = doBrm(uv, 0.0, 0.0, var);
    
    put = 1.0 - put;
    fragColor = vec4(put, put, put, 1.0);
}
'''


class StBrmlabLogo(GlslSource):
    name = 'st_brmlab_logo'
    label = 'Brmlab logo'
    description = 'logo of prague hackerspace brmlab'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
