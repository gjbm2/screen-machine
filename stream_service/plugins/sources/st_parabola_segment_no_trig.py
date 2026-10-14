"""Parabola Segment   No Trig: This is an alternative version to https://www.shadertoy.com/view/3lSczz without trigonometric functions.

Source: https://www.shadertoy.com/view/ttGfRt
Author: edubart
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Reference: http://iquilezles.org/www/articles/trisect/trisect.htm
float trisect(float x) {
	x = sqrt(0.5+0.5*x);
    return x*(-0.064916*x+0.564916)+0.5;
}

float sdParabola( in vec2 pos, in float wi, in float he )
{
    pos.x = abs(pos.x);

    float ik = wi*wi/he;
    float p = ik*(he-pos.y-0.5*ik)/3.0;
    float q = pos.x*ik*ik*0.25;
    
    float h = q*q - p*p*p;

    float x;
    if (h>0.0) { // 1 root
        float r = sqrt(abs(h));
        float qr = q-r;
        x = pow(q+r,1.0/3.0) + pow(abs(qr),1.0/3.0)*sign(qr);
    } else { // 3 roots
        float rp = 1.0/p;
        x = 2.0*trisect(q*rp*sqrt(rp))*sqrt(p);
        //x = 2.0*cos(acos(q*rp*sqrt(rp))/3.0)*sqrt(p);
    }
    
    x = min(x,wi);
    
    return length(pos-vec2(x,he-x*x/ik)) * 
           sign(ik*(pos.y-he)+pos.x*pos.x);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    // animate
    float t = iTime/2.0;
    
	float w = 0.7+0.69*sin(iTime*0.61+0.0);
    float h = 0.4+0.35*sin(iTime*0.53+2.0);
    
    // sdf
    float d = sdParabola( p, w, h );
    
    float ra = 0.2*(0.5+0.5*cos(1.4*iTime));
    d = (cos(0.5*iTime)>0.0) ? abs(d) - ra : d;
    
    
    
    // colorize
    vec3 col = (d>0.0) ? vec3(0.9,0.6,0.3) : vec3(1.0,1.1,1.2);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.7 + 0.2*cos(110.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );
    
    // width and height
    d = length(p-vec2(0.0,h))-0.02;
    d = min(d, length(p-vec2(w,0.0))-0.02);
    col = mix( col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0,0.01,d) );
    
	fragColor = vec4(col,1.0);
}
 '''


class StParabolaSegmentNoTrig(GlslSource):
    name = 'st_parabola_segment_no_trig'
    label = 'Parabola Segment   No Trig'
    description = 'This is an alternative version to https://www.shadertoy.com/view/3lSczz without trigonometric functions.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
