"""Wobbletunnel: Played around a bit with iqs shader

Source: https://www.shadertoy.com/view/wlffzM
Author: tyron
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Forked from https://www.shadertoy.com/view/wtXfRH

// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// A simple way to prevent aliasing of cosine functions (the color
// palette in this case is made of 8 layers) by attenuating them
// when their oscillations become smaller than a pixel. Left is
// direct use of cos(x), right is band-limited cos(x).
//
// Box-filtering of cos(x):
//
// (1/w)∫cos(t)dt with t ∈ (x-½w, x+½w)
// = [sin(x+½w) - sin(x-½w)]/w
// = cos(x)·sin(½w)/(½w)
//
// Can approximate smoothstep(2π,0,w) ≈ sin(w/2)/(w/2),
// which you can also see as attenuating cos(x) when it 
// oscilates more than once per pixel. More info:
//
// https://iquilezles.org/www/articles/bandlimiting/bandlimiting.htm
//
// Related Shader:
//   https://www.shadertoy.com/view/WtScDt
//   https://www.shadertoy.com/view/wtXfRH
//   https://www.shadertoy.com/view/3tScWd



vec3 fcos( vec3 x )
{
    vec3 w = fwidth(x);
    return cos(x) * smoothstep(6.28,0.0,w);
}

vec3 getColor( in float t )
{
    vec3 col = vec3(0.4,0.4,0.4);
    col += 0.12*fcos(6.28318*t*  1.0+vec3(0.0,0.8,1.1));
    col += 0.11*fcos(6.28318*t*  3.1+vec3(0.3,0.4,0.1));
    col += 0.10*fcos(6.28318*t*  5.1+vec3(0.1,0.7,1.1));
    col += 0.09*fcos(6.28318*t*  9.1+vec3(0.2,0.8,1.4));
    col += 0.08*fcos(6.28318*t* 17.1+vec3(0.2,0.6,0.7));
    col += 0.07*fcos(6.28318*t* 31.1+vec3(0.1,0.6,0.7));
    col += 0.06*fcos(6.28318*t* 65.1+vec3(0.0,0.5,0.8));
    col += 0.06*fcos(6.28318*t*115.1+vec3(0.1,0.4,0.7));
    col += 0.09*fcos(6.28318*t*265.1+vec3(1.1,1.4,2.7));
    return col;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord )
{
    // coordiantes
	vec2 p = (2.0*fragCoord-iResolution.xy) / iResolution.y;
   	
    float w = 1.0 + 3.0*abs(2.0 - length(fragCoord.xy / iResolution.xy));
    p.x += (cos(iTime * 0.91) + cos(iTime * 0.24)) / 10.0 * w;
    p.y += (sin(iTime * 0.751) + sin(iTime * 0.42)) / 10.0 * w;
    
    // deform 1
    p *= 0.25;
    p = 0.5*p/dot(p,p);
    vec2 q = p;

    float b = (length(fragCoord.xy) - 0.5) * iTime/ 1.0;

    // deform 2
    p += 0.1*cos( (1.5*p.yx + 0.03*1.0*iTime + vec2(0.1,1.1))  );
	p += 0.05*cos( (2.4*p.yx + 0.03*1.6*iTime + vec2(4.5,2.6))  );
	p += 0.04*cos( (3.3*p.yx + 0.03*1.2*iTime + vec2(3.2,3.4))  );
	p += 0.03*cos( (4.2*p.yx + 0.03*1.7*iTime + vec2(1.8,5.2))  );
	p += 0.06*cos( (9.1*p.yx + 0.03*1.1*iTime + vec2(6.3,3.9)) );

    // base color pattern
    vec3 col = getColor( 0.5*length(p) + iTime*0.6 );
    
    // lighting
    col *= 1.4 - 0.07*length(q);

 
   fragColor = vec4( col, 1.0 );
}'''


class StWobbletunnel(GlslSource):
    name = 'st_wobbletunnel'
    label = 'Wobbletunnel'
    description = 'Played around a bit with iqs shader'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
