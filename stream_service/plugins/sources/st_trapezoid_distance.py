"""Trapezoid   distance: Distance to a capped cone. Useful to make 3D truncated cones. This function is the basis to build and exact SDF to a 3D capped cone (as shown here:...

Source: https://www.shadertoy.com/view/MlycD3
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

// Distance to an isosceles trapezoid

// List of some other 2D distances: https://www.shadertoy.com/playlist/MXdSRf
//
// and www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm



float dot2(in vec2 v ) { return dot(v,v); }

// trapezoid / capped cone, specialized for Y alignment
float sdTrapezoid( in vec2 p, in float r1, float r2, float he )
{
    vec2 k1 = vec2(r2,he);
    vec2 k2 = vec2(r2-r1,2.0*he);

	p.x = abs(p.x);
    vec2 ca = vec2(max(0.0,p.x-((p.y<0.0)?r1:r2)), abs(p.y)-he);
    vec2 cb = p - k1 + k2*clamp( dot(k1-p,k2)/dot2(k2), 0.0, 1.0 );
    
    float s = (cb.x < 0.0 && ca.y < 0.0) ? -1.0 : 1.0;
    
    return s*sqrt( min(dot2(ca),dot2(cb)) );
}

// trapezoid / capped cone
float sdTrapezoid( in vec2 p, in vec2 a, in vec2 b, in float ra, float rb )
{
    float rba  = rb-ra;
    float baba = dot(b-a,b-a);
    float papa = dot(p-a,p-a);
    float paba = dot(p-a,b-a)/baba;
    float x = sqrt( papa - paba*paba*baba );
    float cax = max(0.0,x-((paba<0.5)?ra:rb));
    float cay = abs(paba-0.5)-0.5;
    float k = rba*rba + baba;
    float f = clamp( (rba*(x-ra)+paba*baba)/k, 0.0, 1.0 );
    float cbx = x-ra - f*rba;
    float cby = paba - f;
    float s = (cbx < 0.0 && cay < 0.0) ? -1.0 : 1.0;
    return s*sqrt( min(cax*cax + cay*cay*baba,
                       cbx*cbx + cby*cby*baba) );
}
void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    vec2 m = (2.0*iMouse.xy-iResolution.xy)/iResolution.y;
    
    float ra = 0.2+0.15*sin(iTime*1.3+0.0);
    float rb = 0.2+0.15*sin(iTime*1.4+1.1);
    vec2  pa = vec2(-0.6,0.0)+0.4*sin(iTime*1.1+vec2(0.0,2.0));
    vec2  pb = vec2(-0.6,0.0)+0.4*sin(iTime*1.2+vec2(1.0,2.5));
    vec2  pc = vec2(0.8,0.0);

    // axis aligned trapezoid
	float d = sdTrapezoid( p-pc, ra, rb, 0.5+0.2*sin(1.3*iTime) );
    // aribitrary trapezoid
    d = min( d, sdTrapezoid( p, pa, pb , ra, rb ) );

    
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.8 + 0.2*cos(140.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );
    col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.005,abs(d)) );

    if( iMouse.z>0.001 )
    {
    d = sdTrapezoid( m, pa, pb , ra, rb );
    d = min( d, sdTrapezoid( m-pc, ra, rb, 0.5+0.2*sin(1.3*iTime) ) );
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, abs(length(p-m)-abs(d))-0.0025));
    col = mix(col, vec3(1.0,1.0,0.0), 1.0-smoothstep(0.0, 0.005, length(p-m)-0.015));
    }
    
	fragColor = vec4(col,1.0);
}'''


class StTrapezoidDistance(GlslSource):
    name = 'st_trapezoid_distance'
    label = 'Trapezoid   distance'
    description = 'Distance to a capped cone. Useful to make 3D truncated cones. This function is the basis to build and exact SDF to a 3D capped cone (as shown here:...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
