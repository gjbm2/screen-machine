"""Vesica   gradient 2 D: Signed distance and gradient to a [url=https://www.shadertoy.com/view/XtVfRW]Vesica[/url]. Faster than central differences or automatic differentiation/dual...

Source: https://www.shadertoy.com/view/3lGXRc
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

// Signed distance and gradient to a Vesica. Probably
// faster than central differences or automatic 
// differentiation/dual numbers.

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
vec3 sdgVesica(vec2 p, float r, float d)
{
    vec2 s = sign(p); p = abs(p);

    float b = sqrt(r*r-d*d);  // can delay this sqrt by rewriting the comparison
    
    vec3 res;
    if( (p.y-b)*d > p.x*b )
    {
        vec2  q = vec2(p.x,p.y-b);
        float l = length(q)*sign(d);
        res = vec3( l, q/l );
    }
    else
    {
        vec2  q = vec2(p.x+d,p.y);
        float l = length(q);
        res = vec3( l-r, q/l );
    }
    return vec3(res.x, res.yz*s );
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;

    // animate
    float time = iTime;
    float r1 = 0.5*cos(time+12.0);
    float r2 = 0.2*sin(time*1.4);

    // sdf(p) and gradient(sdf(p))
    vec3  dg = sdgVesica( p, 0.7, r1 );
    float d = dg.x + r2;
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
    
	fragColor = vec4(col,1.0);
}'''


class StVesicaGradient2D(GlslSource):
    name = 'st_vesica_gradient_2_d'
    label = 'Vesica   gradient 2 D'
    description = 'Signed distance and gradient to a [url=https://www.shadertoy.com/view/XtVfRW]Vesica[/url]. Faster than central differences or automatic differentiation/dual...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
