"""Rotational symmetry: Rotational symmetry with regular mod() domain repetition. Since the repeated objects extend past half the repeating space, the one extra neighbor needs to be...

Source: https://www.shadertoy.com/view/XtSczV
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2017 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Rotational symmetry with regular mod() domain repetition. Since the repeated objects extend 
// past half the repeating space, one extra neighbor need to be checked for distance too. This
// can be done cheaply without extra trigonometry (line 46).
//    
// Idea from aiekick's https://www.shadertoy.com/view/XtBcRV but with fixed discontinuities at
// minimal cost, and euclidean distance


const int n = 8;
const float an = 6.2831/float(n);
const mat2 inc = mat2( cos(an),sin(an),-sin(an),cos(an)); // can replace by constants
//const mat2 inc = mat2(1.0,1.0,-1.0,1.0)/1.41421; // same as above for n=8

//#define SIMULATE_BUG
#define BOX_SIZE 0.5

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 q = (fragCoord*2.-iResolution.xy)/iResolution.y*3.;

    float fa = (atan(q.y,q.x)+an*0.5)/an;
    float ia = floor( fa );
    
    float sym = an*ia;
    vec2 r = mat2(cos(sym),-sin(sym),
                  sin(sym), cos(sym))*q;
        
    #ifndef SIMULATE_BUG
    if( fract(fa)>0.5 ) r = r*inc; // rotate one backwards
    #endif

    float d = 4.0 + length(q);
    #ifndef SIMULATE_BUG
    for( int i=0; i<2; i++ )
    #endif
    {
                                            // int id = (int(ia)-i) % n; or float id = mod(ia-float(i),float(n));
        float rot = iTime;                  // + 2.0*float(id); // try this for different angles
        
        // translate and rotate each box
        vec2 p = mat2( cos(rot),sin(rot),
                      -sin(rot),cos(rot))*(r - vec2(2.0,0.0));

        // box distance
        p = abs(p) - BOX_SIZE;                   // + 0.4*sin(3.0*float(id)); // try this for different size
        d = min( d, min(max(p.x,p.y),0.0) + length(max(p,0.0)) );
        
        // next angle
        r = inc*r;
	}
    
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-2.0*abs(d));
	col *= 0.8 + 0.2*cos(120.0*abs(d));
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.02,abs(d)) );

	fragColor = vec4( col, 1.0 );;
}'''


class StRotationalSymmetry(GlslSource):
    name = 'st_rotational_symmetry'
    label = 'Rotational symmetry'
    description = 'Rotational symmetry with regular mod() domain repetition. Since the repeated objects extend past half the repeating space, the one extra neighbor needs to be...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
