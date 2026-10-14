"""Ellipse   gradient 2 D: Signed distance and gradient to an ellipse. Faster than central differences or automatic differentiation/dual numbers most probably.

Source: https://www.shadertoy.com/view/3lcfR8
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2021 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Signed distance and gradient to an ellipse. Probably
// faster than central differences or automatic
// differentiation/dual numbers.
//
// It uses 4 iterations of Newton's root solver, but could need more for
// very eccentric ellipses (see line 46). For an analytic solver see
// https://www.shadertoy.com/view/4sS3zz

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
// Ellipse:            https://www.shadertoy.com/view/3lcfR8
// Smooth-Minimum:     https://www.shadertoy.com/view/tdGBDt

// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .yz = ∇f(p) with ‖∇f(p)‖ = 1
vec3 sdgEllipse( vec2 p, in vec2 ab )
{
    // symmetry
    vec2 sp = sign(p);
	p = abs( p );
    
    // determine in/out and initial value
    bool s = dot(p/ab,p/ab)>1.0;
	float w = atan(p.y*ab.x, p.x*ab.y);
    if(!s) w=(ab.x*(p.x-ab.x)<ab.y*(p.y-ab.y))? 1.570796327 : 0.0;
    
    // Newton root solver
    for( int i=0; i<4; i++ )
    {
        vec2 cs = vec2(cos(w),sin(w));
        vec2 u = ab*vec2( cs.x,cs.y);
        vec2 v = ab*vec2(-cs.y,cs.x);
        w = w + dot(p-u,v)/(dot(p-u,u)+dot(v,v));
    }
    vec2  q = ab*vec2(cos(w),sin(w));

    // compute distance and gradient (everything above
    // could probably be replaced by something better)
    float d = length(p-q);
    return vec3( d, sp*(p-q)/d ) * (s?1.0:-1.0);
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
        vec2 ab = 0.7 + vec2(0.4,0.2)*cos(iTime*0.52+vec2(1,2));

        // sdf(p) and gradient(sdf(p))
        vec3  dg = sdgEllipse(p,ab);
        float d = dg.x;
        vec2  g = dg.yz;

        // central differenes based gradient, for comparison
        //g = vec2(dFdx(d),dFdy(d))/(2.0/iResolution.y);

        // coloring
        vec3 col = (d>0.0) ? vec3(0.9,0.6,0.3) : vec3(0.4,0.7,0.85);
        col *= 1.0 + vec3(0.5*g,0.0);
      //col = vec3(0.5+0.5*g,1.0);
        col *= 1.0 - 0.5*exp(-16.0*abs(d));
        col *= 0.9 + 0.1*cos(150.0*d);
        col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );
        
        // draw gradient discontinuty
        if( sin(3.1415927*iTime)>0.0 )
        {
            float f2 = ab.x*ab.x - ab.y*ab.y;
            if( ab.x>ab.y )
            {
                float foc = f2/ab.x;
                p.x -= clamp(p.x,-foc,foc);
            }
            else
            {
                float foc = -f2/ab.y;
                p.y -= clamp(p.y,-foc,foc);
            }
            d = length(p);
            col = mix( col, vec3(1.0), 1.0-smoothstep(0.005,0.010,abs(d)) );
        }
    
	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

	fragColor = vec4( tot, 1.0 );
}'''


class StEllipseGradient2D(GlslSource):
    name = 'st_ellipse_gradient_2_d'
    label = 'Ellipse   gradient 2 D'
    description = 'Signed distance and gradient to an ellipse. Faster than central differences or automatic differentiation/dual numbers most probably.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
