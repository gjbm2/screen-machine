"""Simple Egg   distance 2 D: Distance to an egg shape (a base circle of radius ra, a tip circle of radius rb, and two circles connecting both). It is not general enough since the height...

Source: https://www.shadertoy.com/view/Wdjfz3
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

// A simple generaliztion of sylvain69780's shader "Moss's EGG":
// https://www.shadertoy.com/view/wsBBR3
//
// A more powerful generalization is possible where the height of the egg
// is also user specified, and the tangent/contact points are computed
// instead of hardcoded.
//
// This is also a special case of the Vesica primitive, in particular
// this egg is just half a vesica with a circle attached at its end
// https://www.shadertoy.com/view/XtVfRW

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and http://www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm



float sdEgg( in vec2 p, in float ra, in float rb )
{
    const float k = sqrt(3.0);
    
    p.x = abs(p.x);
    
    float r = ra - rb;

    return ((p.y<0.0)       ? length(vec2(p.x,  p.y    )) - r :
            (k*(p.x+r)<p.y) ? length(vec2(p.x,  p.y-k*r)) :
                              length(vec2(p.x+r,p.y    )) - 2.0*r) - rb;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // normalized pixel coordinates
    vec2 p = (fragCoord*2.0-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
    
    // animation
    float ra = 0.6;
    float rb = ra*(0.55+0.45*cos(2.0*iTime));
    
    // distance
    float d = sdEgg(p+vec2(0.,0.2), ra, rb);
    
    // coloring
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.8 + 0.2*cos(128.0*abs(d));
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdEgg(m+vec2(0.,0.2), ra, rb);
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }

	fragColor = vec4(col, 1.0);
}'''


class StSimpleEggDistance2D(GlslSource):
    name = 'st_simple_egg_distance_2_d'
    label = 'Simple Egg   distance 2 D'
    description = 'Distance to an egg shape (a base circle of radius ra, a tip circle of radius rb, and two circles connecting both). It is not general enough since the height...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
