"""Wave   distance: Distance to an arbitrarily rounded triangle wave with arbitrary slope.

Source: https://www.shadertoy.com/view/7lfXDn
Author: rodolphito
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2021 Rodol Phito
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Distance to a squiggly line/triangle wave
// Heavily based on IQ's work obviously, only the actual sdf is original here

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm

// c must be a positive unit vector pointing along the triangle's slope
float sdWave( in vec2 p, in float r, in vec2 c )
{
    p.x = abs(mod(p.x,2.0)-1.0)-0.5;
    float d = dot(p,c);
    if (abs(d)*c.x+r*c.y-0.5 > 0.0)
    {
        float s = (d > 0.0) ? 1.0 : -1.0;
        p -= s*vec2(0.5,(0.5*c.y-r)/c.x);
        return s*(r-length(p));
    }
    return dot(p,vec2(c.y,-c.x));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // normalized pixel coordinates
    vec2 p = 2.*(fragCoord*2.0-iResolution.xy)/iResolution.y;
    vec2 m = 2.*(2.0*iMouse.xy-iResolution.xy)/iResolution.y;
 
    // animation
    float time = iTime;
    float r = 0.25+0.25*cos(time*0.41+3.0);
    float t = 3.*(0.5+0.5*cos(time*0.8));
    vec2 c = normalize(vec2(1.,t + 1e-3));
    // radius can be larger when slope is shallower
    r /= c.y;
    
    // distance
    float d = sdWave(p, r, c);
    
    // coloring
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-2.0*abs(d));
	col *= 0.8 + 0.2*cos(128.0*abs(d));
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdWave(m, r, c);
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }

	fragColor = vec4(col, 1.0);
}'''


class StWaveDistance(GlslSource):
    name = 'st_wave_distance'
    label = 'Wave   distance'
    description = 'Distance to an arbitrarily rounded triangle wave with arbitrary slope.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
