"""Trapezoid SDF flickering fix: I've been looking into the 2D Trapezoid and I noticed a very small one pixel line in the center of the trapezoid primitive that were flickering. Left: Original...

Source: https://www.shadertoy.com/view/wtSyWc
Author: bloxard
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
// By Per Bloksgaard/2020

// Original Trapezoid from https://www.shadertoy.com/view/MlycD3
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

// Slightly improved version of a Trapezoid primitive without flicking.
float sdTrapezoidFixed(in vec2 p, in vec2 a, in vec2 b, in float ra, float rb)
{
    vec2 pa = p - a;
    vec2 ba = b - a;
    float baba = dot(ba, ba);
    float x = abs(dot(vec2(-pa.y, pa.x), ba)) / sqrt(baba);
    float paba = dot(pa, ba) / baba;
    float rba = rb - ra;
    float cax = max(0., x - ((paba < 0.5) ? ra : rb));
    float cay = abs(paba - 0.5) - 0.5;
    float f = clamp((rba*(x - ra) + paba * baba) / (rba * rba + baba), 0., 1.);
    float cbx = x - ra - f * rba;
    float cby = paba - f;
    return sign(max(cbx,cay)) * sqrt(min(cax*cax + cay * cay*baba, cbx*cbx + cby * cby*baba));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 p = (-iResolution.xy+2.*fragCoord.xy)/iResolution.y*0.5;
    float ra = 0.13-0.1*abs(sin(iTime*1.37+0.0));
    float rb = 0.13-0.1*abs(sin(iTime*1.41+1.1));
    vec2 pa = vec2(-0.3 + 0.05*sin(iTime*1.3), -0.2 + -0.15*cos(iTime*0.4));
    vec2 pb = vec2(0.3 - 0.05*sin(iTime*-0.3), 0.2 + 0.15*cos(iTime*0.37));
    float a = iTime * 0.7;
    mat2 m = mat2(cos(a),sin(a),-sin(a),cos(a));
    pa *= m;
    pb *= m;
	float mX = (iMouse.z>0.) ? ((iMouse.x/iResolution.x*2.-1.)*iResolution.x/iResolution.y)*0.5 : 0.;
    float d = 1e6;
    if (p.x < mX)
    {
        d = sdTrapezoid(p, pa, pb, ra, rb);
    }
    else
    {
        d = sdTrapezoidFixed(p, pa, pb, ra, rb);
    }
    vec3 col = mix(vec3(0.,0.,1.), vec3(1.,1.,0.),sign(d));
    col *= 1. - exp(-4.0*abs(d));
    col *= 0.8 + 0.2*cos(140.*d);
    col = mix(col, vec3(1.), 1. - smoothstep(0., 0.015, abs(d)));
    col = mix(col, vec3(1.), 1. - smoothstep(0., 0.005, abs(d)));
    col = mix(col, vec3(1.), 1.0 - smoothstep(0., 0.005, abs(p.x - mX)));
    fragColor = vec4(col,1.);
}
'''


class StTrapezoidSdfFlickeringFix(GlslSource):
    name = 'st_trapezoid_sdf_flickering_fix'
    label = 'Trapezoid SDF flickering fix'
    description = "I've been looking into the 2D Trapezoid and I noticed a very small one pixel line in the center of the trapezoid primitive that were flickering. Left: Original..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
