"""Arc   gradient 2 D: Signed distance and gradient to an arc segment. Faster than central differences or automatic differentiation/dual numbers most probably.

Source: https://www.shadertoy.com/view/WtGXRc
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Signed distance and gradient to a circle art.
// Probably faster than central differences or automatic
// differentiation/dual numbers.

// List of other 2D distances+gradients:
// https://iquilezles.org/www/articles/distgradfunctions2d/distgradfunctions2d.htm
//
// Circle:             https://www.shadertoy.com/view/WltSDj
// Pie:                https://www.shadertoy.com/view/3tGXRc
// Arc:                https://www.shadertoy.com/view/WtGXRc
// Isosceles Triangle: https://www.shadertoy.com/view/3dyfDd
// Triangle:           https://www.shadertoy.com/view/tlVyWh
// Box:                https://www.shadertoy.com/view/wlcXD2
// Quad:               https://www.shadertoy.com/view/WtVcD1
// Cross:              https://www.shadertoy.com/view/WtdXWj
// Segment:            https://www.shadertoy.com/view/WtdSDj
// Hexagon:            https://www.shadertoy.com/view/WtySRc
// Vesica:             https://www.shadertoy.com/view/3lGXRc
// Smooth-Minimum:     https://www.shadertoy.com/view/tdGBDt
// Parallelogram:      https://www.shadertoy.com/view/sssGzX

// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .yz = ∇f(p) with ‖∇f(p)‖ = 1
// sca is the sin/cos of the orientation
// scb is the sin/cos of the aperture
vec3 sdgArc( in vec2 p, in vec2 sca, in vec2 scb, in float ra, in float rb )
{
    vec2 q = p;

    mat2 ma = mat2(sca.x,-sca.y,sca.y,sca.x);
    p = ma*p;

    float s = sign(p.x); p.x = abs(p.x);
    
    if( scb.y*p.x > scb.x*p.y )
    {
        vec2  w = p - ra*scb;
        float d = length(w);
        return vec3( d-rb, vec2(s*w.x,w.y)*ma/d );
    }
    else
    {
        float l = length(q);
        float w = l - ra;
        return vec3( abs(w)-rb, sign(w)*q/l );
    }
}

#define AA 2

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 tot = vec3(0.0);
    
    #if AA>1
    for( int m=0; m<AA; m++ )
    for( int n=0; n<AA; n++ )
    {
        // pixel coordinates
        vec2 o = vec2(float(m),float(n)) / float(AA) - 0.5;
        vec2 p = (-iResolution.xy + 2.0*(fragCoord+o))/iResolution.y;
        #else    
        vec2 p = (-iResolution.xy + 2.0*fragCoord)/iResolution.y;
        #endif

        // animation
        float ta = 3.14*(0.5+0.5*cos(iTime*0.52+2.0));
        float tb = 3.14*(0.5+0.5*cos(iTime*0.31+2.0));
        float rb = 0.15*(0.5+0.5*cos(iTime*0.41+1.0));

        // sdf(p) and gradient(sdf(p))
        vec3  dg = sdgArc(p,vec2(sin(ta),cos(ta)),vec2(sin(tb),cos(tb)), 0.5, rb);
        float d = dg.x;
        vec2  g = dg.yz;

        // central differenes based gradient, for comparison
        // g = vec2(dFdx(d),dFdy(d))/(2.0/iResolution.y);

        // coloring
        vec3 col = (d>0.0) ? vec3(0.9,0.6,0.3) : vec3(0.4,0.7,0.85);
        col *= 1.0 + vec3(0.5*g,0.0);
      //col = vec3(0.5+0.5*g,1.0);
        col *= 1.0 - 0.5*exp(-16.0*abs(d));
        col *= 0.9 + 0.1*cos(150.0*d);
        col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );
    
	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

	fragColor = vec4( tot, 1.0 );
}'''


class StArcGradient2D(GlslSource):
    name = 'st_arc_gradient_2_d'
    label = 'Arc   gradient 2 D'
    description = 'Signed distance and gradient to an arc segment. Faster than central differences or automatic differentiation/dual numbers most probably.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
