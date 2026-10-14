"""Box SDF (from i Q's website): SIGNED distance to a non-rounded box, with a long comment describing what's going on

Source: https://www.shadertoy.com/view/tdGSDd
Author: mstange
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2015 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Signed distance to a 2D rounded box

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


// (0, 0) is the center of the box.
// The four corners of the box are (+/- b.x, +/- b.y).
// d is the vector between p and the closest box corner,
// mapped into the positive quadrant: negative values
// for dimensions inside the box and positive values for
// dimensions outside the box.
// max(d, vec2(0)) is 0 for dimensions inside the box
// and negative for dimensions outside the box.
// length(max(d,vec2(0))) is 0 if p is inside the box and
// the distance from the closest point inside the box if
// p is outside the box.
// If p is outside the box, max(d.x,d.y) is positive and
// min(max(d.x,d.y),0.0) is zero.
// If p is inside the box, max(d.x,d.y) is the negative
// distance from p to the closest point on an edge of the box,
// and min(max(d.x,d.y),0.0) is the same.

float sdBox( in vec2 p, in vec2 b )
{
    vec2 d = abs(p)-b;
    return length(max(d,vec2(0))) + min(max(d.x,d.y),0.0);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;

	vec2 ra = 0.4 + 0.3*cos( iTime + vec2(0.0,1.57) + 0.0 );

	float d = sdBox( p, ra);

    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-2.0*abs(d));
	col *= 0.8 + 0.2*cos(150.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.02,abs(d)) );

	fragColor = vec4(col,1.0);
}'''


class StBoxSdfFromIQSWebsite(GlslSource):
    name = 'st_box_sdf_from_i_q_s_website'
    label = "Box SDF (from i Q's website)"
    description = "SIGNED distance to a non-rounded box, with a long comment describing what's going on"
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
