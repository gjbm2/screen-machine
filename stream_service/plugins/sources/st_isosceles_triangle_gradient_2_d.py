"""Isosceles Triangle   gradient 2 D: Signed distance and analytic gradient to an isosceles triangle. Faster than central differences or automatic differentiation/dual numbers most probably.

Source: https://www.shadertoy.com/view/3dyfDd
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

// Signed distance and analytic gradient to an isosceles
// triangle. More accurate than central differences and
// faster to compute than automatic differentiation/duals.

// List of other 2D distances+gradients:
//
// https://iquilezles.org/www/articles/distgradfunctions2d/distgradfunctions2d.htm
//
// and
//
// https://www.shadertoy.com/playlist/M3dSRf


// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .yz = ∇f(p) with ‖∇f(p)‖ = 1
vec3 sdgTriangleIsosceles( in vec2 p, in vec2 q )
{
    float w = sign(p.x);
    p.x = abs(p.x);
	vec2 a = p - q*clamp( dot(p,q)/dot(q,q), 0.0, 1.0 );
    vec2 b = p - q*vec2( clamp( p.x/q.x, 0.0, 1.0 ), 1.0 );
    float k = sign( q.y );
    float l1 = dot(a,a);
    float l2 = dot(b,b);
    float d = sqrt((l1<l2)?l1:l2);
    vec2  g =      (l1<l2)? a: b;
    float s = max( k*(p.x*q.y-p.y*q.x),k*(p.y-q.y)  );
    return vec3(d,vec2(w*g.x,g.y)/d)*sign(s);
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
        
        p.y -= 0.3;
        // size
        vec2 si = vec2(0.5,-0.5) + vec2(0.3,-0.3)*cos( iTime + vec2(0.0,1.57) + 0.0 );

        // sdf(p) and gradient(sdf(p))
        vec3 dg = sdgTriangleIsosceles(p,si);
        float d = dg.x;
        vec2 g = dg.yz;

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


class StIsoscelesTriangleGradient2D(GlslSource):
    name = 'st_isosceles_triangle_gradient_2_d'
    label = 'Isosceles Triangle   gradient 2 D'
    description = 'Signed distance and analytic gradient to an isosceles triangle. Faster than central differences or automatic differentiation/dual numbers most probably.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
