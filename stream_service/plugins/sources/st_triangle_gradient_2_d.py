"""Triangle   gradient 2 D: Signed distance and gradient to a (rounded) triangle. Faster than central differences or automatic differentiation/dual numbers most probably.

Source: https://www.shadertoy.com/view/tlVyWh
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

// Signed distance and gradient to a triangle. Probably
// faster than central differences or automatic 
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

float cro( in vec2 a, in vec2 b ) { return a.x*b.y - a.y*b.x; }

// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .yz = ∇f(p) with ‖∇f(p)‖ = 1
vec3 sdgTriangle( in vec2 p, in vec2 v[3] )
{
    float gs = cro(v[0]-v[2],v[1]-v[0]);
    vec4 res;
    
    // edge 0
    {
    vec2  e = v[1]-v[0];
    vec2  w = p-v[0];
    vec2  q = w-e*clamp(dot(w,e)/dot(e,e),0.0,1.0);
    float d = dot(q,q);
    float s = gs*cro(w,e);
    res = vec4(d,q,s);
    }
    
    // edge 1
    {
	vec2  e = v[2]-v[1];
    vec2  w = p-v[1];
    vec2  q = w-e*clamp(dot(w,e)/dot(e,e),0.0,1.0);
    float d = dot(q,q);
    float s = gs*cro(w,e);
    res = vec4( (d<res.x) ? vec3(d,q) : res.xyz,
                (s>res.w) ?      s    : res.w );
    }
    
    // edge 2
    {
	vec2  e = v[0]-v[2];
    vec2  w = p-v[2];
    vec2  q = w-e*clamp(dot(w,e)/dot(e,e),0.0,1.0);
    float d = dot(q,q);
    float s = gs*cro(w,e);
    res = vec4( (d<res.x) ? vec3(d,q) : res.xyz,
                (s>res.w) ?      s    : res.w );
    }
    
    // distance and sign
    float d = sqrt(res.x)*sign(res.w);
    
    return vec3(d,res.yz/d);

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

        // animate
        float time = iTime;
        vec2 v[3] = vec2[3](
            vec2(-0.8,-0.3) + 0.5*cos( 0.5*time + vec2(0.0,1.9) + 4.0 ),
            vec2( 0.8,-0.3) + 0.5*cos( 0.7*time + vec2(0.0,1.7) + 2.0 ),
            vec2( 0.0, 0.3) + 0.5*cos( 0.9*time + vec2(0.0,1.3) + 1.0 ) );

        // corner radious
        float ra = 0.1*(0.5+0.5*sin(iTime*1.2));

        // sdf(p) and gradient(sdf(p))
        vec3  dg = sdgTriangle(p,v);
        float d = dg.x-ra;
        vec2  g = dg.yz;

        // central differenes based gradient, for comparison
        // g = vec2(dFdx(d),dFdy(d))/(2.0/iResolution.y);

        // coloring
        vec3 col = (d>0.0) ? vec3(0.9,0.6,0.3) : vec3(0.4,0.7,0.85);
        col *= 1.0 + vec3(0.5*g,0.0);
      //col = vec3(0.5+0.5*g,1.0);
        col *= 1.0 - 0.7*exp(-8.0*abs(d));
        col *= 0.9 + 0.1*cos(150.0*d);
        col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );

 	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

	fragColor = vec4( tot, 1.0 );
}'''


class StTriangleGradient2D(GlslSource):
    name = 'st_triangle_gradient_2_d'
    label = 'Triangle   gradient 2 D'
    description = 'Signed distance and gradient to a (rounded) triangle. Faster than central differences or automatic differentiation/dual numbers most probably.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
