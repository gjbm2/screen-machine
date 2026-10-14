"""Arrow   distance: Distance to an arrow (exact interior and exterior Euclidean distances)

Source: https://www.shadertoy.com/view/slj3Dd
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

// Distance to an arrow

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm


// The arrow goes from a to b. It's thickness is w1. The arrow
// head's thickness is w2.
float sdArrow( in vec2 p, vec2 a, vec2 b, float w1, float w2 )
{
    // return min(length(p-a)-w1,length(p-b)); for debugging

    // constant setup
    const float k = 3.0;   // arrow head ratio
	vec2  ba = b - a;
    float l2 = dot(ba,ba);
    float l = sqrt(l2);

    // pixel setup
    p = p-a;
    p = mat2(ba.x,-ba.y,ba.y,ba.x)*p/l;
    p.y = abs(p.y);
    vec2 pz = p-vec2(l-w2*k,w2);

    // === distance (four segments) === 

    vec2 q = p;
    q.x -= clamp( q.x, 0.0, l-w2*k );
    q.y -= w1;
    float di = dot(q,q);
    //----
    q = pz;
    q.y -= clamp( q.y, w1-w2, 0.0 );
    di = min( di, dot(q,q) );
    //----
    if( p.x<w1 ) // conditional is optional
    {
    q = p;
    q.y -= clamp( q.y, 0.0, w1 );
    di = min( di, dot(q,q) );
    }
    //----
    if( pz.x>0.0 ) // conditional is optional
    {
    q = pz;
    q -= vec2(k,-1.0)*clamp( (q.x*k-q.y)/(k*k+1.0), 0.0, w2 );
    di = min( di, dot(q,q) );
    }
    
    // === sign === 
    
    float si = 1.0;
    float z = l - p.x;
    if( min(p.x,z)>0.0 ) //if( p.x>0.0 && z>0.0 )
    {
      float h = (pz.x<0.0) ? w1 : z/k;
      if( p.y<h ) si = -1.0;
    }
    return si*sqrt(di);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // normalized pixel coordinates
    vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
    
    // animation
    float time = iTime*0.25;
    vec2 a = vec2(-1.0,0.0)+vec2(0.4,0.6)*cos(time*vec2(1.1,1.3)+vec2(0.0,1.0));
    vec2 b = vec2( 1.0,0.0)+vec2(0.4,0.6)*cos(time*vec2(1.2,1.5)+vec2(0.3,2.0));
    float w1 = 0.2;//0.05*(0.8+0.2*cos(time*0.31+2.0));
    float w2 = w1 + 0.15;
    
    // distance
    float d = sdArrow(p, a, b, w1, w2);
    
    // coloring
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-5.0*abs(d));
	col *= 0.8 + 0.2*cos(128.0*abs(d));
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdArrow(m, a, b, w1, w2);
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }

	fragColor = vec4(col, 1.0);
}'''


class StArrowDistance(GlslSource):
    name = 'st_arrow_distance'
    label = 'Arrow   distance'
    description = 'Distance to an arrow (exact interior and exterior Euclidean distances)'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
