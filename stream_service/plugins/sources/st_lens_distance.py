"""Lens   distance: This is a derivative of Inigo Quilez's SDF vesica function. It offers a "lens" that is a reparameterization of a vesica that you specify by it's half-height...

Source: https://www.shadertoy.com/view/ws3SD2
Author: shanecelis
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2018 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Distance to an vesica (intersection of two disks). Can be seen as a
// special case of abje's crescent shape (https://www.shadertoy.com/view/XlVfz1)


// List of some other 2D distances:
//
// Triangle:             https://www.shadertoy.com/view/XsXSz4
// Isosceles Triangle:   https://www.shadertoy.com/view/MldcD7
// Regular Triangle:     https://www.shadertoy.com/view/Xl2yDW
// Regular Pentagon:     https://www.shadertoy.com/view/llVyWW
// Regular Octogon:      https://www.shadertoy.com/view/llGfDG
// Rounded Rectangle:    https://www.shadertoy.com/view/4llXD7
// Rhombus:              https://www.shadertoy.com/view/XdXcRB
// Trapezoid:            https://www.shadertoy.com/view/MlycD3
// Polygon:              https://www.shadertoy.com/view/wdBXRW
// Hexagram:             https://www.shadertoy.com/view/tt23RR
// Regular Star:         https://www.shadertoy.com/view/3tSGDy
// Ellipse 1:            https://www.shadertoy.com/view/4sS3zz
// Ellipse 2:            https://www.shadertoy.com/view/4lsXDN
// Quadratic Bezier:     https://www.shadertoy.com/view/MlKcDD
// Uneven Capsule:       https://www.shadertoy.com/view/4lcBWn
// Vesica:               https://www.shadertoy.com/view/XtVfRW
// Cross:                https://www.shadertoy.com/view/XtGfzw
// Pie:                  https://www.shadertoy.com/view/3l23RK
// Arc:                  https://www.shadertoy.com/view/wl23RK
// Horseshoe:            https://www.shadertoy.com/view/WlSGW1
// Parabola:             https://www.shadertoy.com/view/ws3GD7
//
// and many more here:   http://www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm


float sdVesica(vec2 p, float r, float d)
{
    p = abs(p);

    float b = sqrt(r*r-d*d);  // can delay this sqrt by rewriting the comparison
    return ((p.y-b)*d > p.x*b) ? length(p-vec2(0.0,b))
                               : length(p-vec2(-d,0.0))-r;
}

/** This is a different way of specifying the vesica. We specify the half-height h
    and its width w. This allows us to maintain the same height of the vesica, which is
    good if you're trying to simulate eyelids and things. -Shane Celis */

float sdLens(vec2 p, float h, float w)
{
 	float d = h / w - w / 4.0;
    float r = w / 2.0 + d;
    return sdVesica(p, r, d);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;

    float d = sdLens( p, 0.8, 0.8 + 0.2*sin(iTime) );

    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.8 + 0.2*cos(140.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );
    col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.005,abs(d)) );

    
	fragColor = vec4(col,1.0);
}'''


class StLensDistance(GlslSource):
    name = 'st_lens_distance'
    label = 'Lens   distance'
    description = 'This is a derivative of Inigo Quilez\'s SDF vesica function. It offers a "lens" that is a reparameterization of a vesica that you specify by it\'s half-height...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
