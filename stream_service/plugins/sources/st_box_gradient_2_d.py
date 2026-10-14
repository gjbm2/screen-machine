"""Box   gradient 2 D: Signed distance and gradient to a box. Faster than central differences or automatic differentiation/dual numbers most probably.

Source: https://www.shadertoy.com/view/wlcXD2
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

// Signed distance and gradient to a box. Probably
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


// Other Box functions
// http://iquilezles.org/www/articles/boxfunctions/boxfunctions.htm
//
// Intersection:     https://www.shadertoy.com/view/ld23DV
// Occlusion:        https://www.shadertoy.com/view/4sSXDV
// Occlusion:        https://www.shadertoy.com/view/4djXDy
// Density:          https://www.shadertoy.com/view/Ml3GR8
// Fake soft shadow: https://www.shadertoy.com/view/WslGz4
// Gradient:         https://www.shadertoy.com/view/wlcXD2


// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .yz = ∇f(p) with ‖∇f(p)‖ = 1
vec3 sdgBox( in vec2 p, in vec2 b )
{
    vec2 w = abs(p)-b;
    vec2 s = vec2(p.x<0.0?-1:1,p.y<0.0?-1:1);
    
    float g = max(w.x,w.y);
	vec2  q = max(w,0.0);
    float l = length(q);
    
    return vec3(   (g>0.0)?l   : g,
                s*((g>0.0)?q/l : ((w.x>w.y)?vec2(1,0):vec2(0,1))));
}


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;

    // corner radious
    float ra = 0.1*(0.5+0.5*sin(iTime*1.2));

    // sdf(p) and gradient(sdf(p))
    vec3  dg = sdgBox(p,vec2(0.8,0.3));
    float d = dg.x-ra;
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
    
	fragColor = vec4(col,1.0);
}'''


class StBoxGradient2D(GlslSource):
    name = 'st_box_gradient_2_d'
    label = 'Box   gradient 2 D'
    description = 'Signed distance and gradient to a box. Faster than central differences or automatic differentiation/dual numbers most probably.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
