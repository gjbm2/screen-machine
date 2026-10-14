"""Vesica   distance 2 D: Distance to a vesica shape (intersection of two disks). Can be seen as a special case of abje's crescent shape (https://www.shadertoy.com/view/XlVfz1)

Source: https://www.shadertoy.com/view/XtVfRW
Author: iq
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

// Note, interor distance is ont correct

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and http://www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htmistfunctions2d.htm


float sdVesica(vec2 p, float r, float d)
{
    p = abs(p);

    float b = sqrt(r*r-d*d);  // can delay this sqrt by rewriting the comparison
    return ((p.y-b)*d > p.x*b) ? length(p-vec2(0.0,b))*sign(d)
                               : length(p-vec2(-d,0.0))-r;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;

    float ra1 =       0.5*cos(iTime*1.1*0.5+0.5);
    float ra2 = 0.1 + 0.1*sin(iTime*2.7*0.5+6.0);
    float wid = max(0.5,ra1);
    
	float d = sdVesica( p, wid, ra1 ) - ra2;

    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.8 + 0.2*cos(140.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );
    col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.005,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdVesica(m, wid, ra1 ) - ra2;
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }
   
	fragColor = vec4(col,1.0);
}'''


class StVesicaDistance2D(GlslSource):
    name = 'st_vesica_distance_2_d'
    label = 'Vesica   distance 2 D'
    description = "Distance to a vesica shape (intersection of two disks). Can be seen as a special case of abje's crescent shape (https://www.shadertoy.com/view/XlVfz1)"
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
