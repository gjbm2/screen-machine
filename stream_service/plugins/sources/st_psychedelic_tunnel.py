"""Psychedelic tunnel: Forked from iq,

Source: https://www.shadertoy.com/view/st2XDW
Author: SnoopethDuckDuck
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2013 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


// Simplex Noise (http://en.wikipedia.org/wiki/Simplex_noise), a type of gradient noise
// that uses N+1 vertices for random gradient interpolation instead of 2^N as in regular
// latice based Gradient Noise.


// Value    Noise 2D, Derivatives: https://www.shadertoy.com/view/4dXBRH
// Gradient Noise 2D, Derivatives: https://www.shadertoy.com/view/XdXBRH
// Value    Noise 3D, Derivatives: https://www.shadertoy.com/view/XsXfRH
// Gradient Noise 3D, Derivatives: https://www.shadertoy.com/view/4dffRH
// Value    Noise 2D             : https://www.shadertoy.com/view/lsf3WH
// Value    Noise 3D             : https://www.shadertoy.com/view/4sfGzS
// Gradient Noise 2D             : https://www.shadertoy.com/view/XdXGW8
// Gradient Noise 3D             : https://www.shadertoy.com/view/Xsl3Dl
// Simplex  Noise 2D             : https://www.shadertoy.com/view/Msf3WH
// Wave     Noise 2D             : https://www.shadertoy.com/view/tldSRj
const float tau = 2. * 3.14159;

vec2 hash( vec2 p ) // replace this by something better
{
	p = vec2( dot(p,vec2(127.1,311.7)), dot(p,vec2(269.5,183.3)) );
	return -1.0 + 2.0*fract(sin(p)*43758.5453123);
}

float noise( in vec2 p )
{
    const float K1 = 0.366025404; // (sqrt(3)-1)/2;
    const float K2 = 0.211324865; // (3-sqrt(3))/6;

	vec2  i = floor( p + (p.x+p.y)*K1 );
    vec2  a = p - i + (i.x+i.y)*K2;
    float m = step(a.y,a.x); 
    vec2  o = vec2(m,1.0-m);
    vec2  b = a - o + K2;
	vec2  c = a - 1.0 + 2.0*K2;
    vec3  h = max( 0.5-vec3(dot(a,a), dot(b,b), dot(c,c) ), 0.0 );
	vec3  n = h*h*h*h*vec3( dot(a,hash(i+0.0)), dot(b,hash(i+o)), dot(c,hash(i+1.0)));
    return dot( n, vec3(70.0) );
}

vec4 colorMap(vec3 a, vec3 b, vec3 c, vec3 d, float t) {
vec3 col = a + b * vec3(cos(tau * (c.r * t + d.r)),
                        cos(tau * (c.g * t + d.g)),
                        cos(tau * (c.b * t + d.b)));

return vec4(col.r, col.g, col.b, 1.);
}

float myLength(vec2 uv) {
return max(abs(uv.x),abs(uv.y));
}

// -----------------------------------------------

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 p = fragCoord.xy / iResolution.xy;

	vec2 uv = p* vec2(iResolution.x/iResolution.y,1. + 0.05 * cos(0.1 * iTime)) + iTime*0.0001;
	float b = 0.5 * (1. + cos(0.25 * iTime));
    float time = 0.2 * iTime;
    vec2 uvShift = uv - 0.5 - 0.0001 *iTime;
    mat2 ma = (4. * iTime + 1.) * mat2( 1.6,  1.2 , -1.2,  1.6 ) 
    * mat2(cos(time),-sin(time),sin(time),cos(time));
    mat2 ma2 =  (4. * iTime + 30.) * mat2( 1.6,  -1.2, 1.2,  1.6 );
    uv /= b * myLength(ma * uvShift) + (1.-b) * length(ma * uvShift);
	float f = 0.0;

		uv = uv * 3.0 + 100.;
        mat2 m = (1. + 0.01 * noise(uv + 0.04 * iTime) + 0.005 * noise(uv + 0.03 * iTime)) * mat2( 1.6,  1.2, -1.2,  1.6 );
		f  = 0.800*noise( uv / length(uv) ); uv = m*uv;
		f += 0.100*noise( uv ); uv = m*uv;
		f += 0.050*noise( uv ); uv = m*uv;
		f += 0.025*noise( uv ); uv = m*uv;
        
     // 0.5 * pow(iTime,10.) -> iTime (or something probably works better)
	f = (4. + cos(2. * iTime) * noise(0.001*vec2(iTime,uv.y * uv.x)) *  0.25 * iTime) * f * (1.-f) * f * (1.-f);
   // f *= length(0.1 * uv);
vec3 h = vec3(0.5, 0.5, 0.5);
float t = 0.1 * iTime;
float b1 = 0.25 * (1. + cos(t));
float b2 = 0.25 * (1. + cos(t + tau / 3.));
float b3 = 0.25 * (1. + cos(t + 2. * tau / 3.));
fragColor = colorMap(h, h, h,  vec3(0.4,0.2,0.), f);

}'''


class StPsychedelicTunnel(GlslSource):
    name = 'st_psychedelic_tunnel'
    label = 'Psychedelic tunnel'
    description = 'Forked from iq,'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
