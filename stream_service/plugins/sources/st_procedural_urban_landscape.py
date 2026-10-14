"""Procedural urban landscape: 

Source: https://www.shadertoy.com/view/Xl2yWh
Author: PrzemyslawZaworski
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License https://opensource.org/licenses/MIT
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Shader code:
// The MIT License https://opensource.org/licenses/MIT
// Copyright © 2017 Przemyslaw Zaworski
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

//#define colorize

mat3 rotationX( float x) 
{
	return mat3
	(
		1.0,0.0,0.0,
		0.0,cos(x),sin(x),
		0.0,-sin(x),cos(x)
	);
}			

float hash (vec2 n) 
{ 
	return fract(sin(dot(n, vec2(12.9898, 4.1414)))*43758.5453);
}

vec3 hash3( vec2 p )
{
    vec3 q = vec3( dot(p,vec2(127.1,311.7)), dot(p,vec2(269.5,183.3)), dot(p,vec2(419.2,371.9)) );
	return fract(sin(q)*43758.5453);
}

float map (vec3 p)
{
	vec2 u = floor(p.xz*0.005*64.0)/64.0;
	float h = hash(u);
	h = p.y - 8.0 * h * h;
	return max( min( h, 0.2), p.y-8.0 );
}
			
vec4 color (vec3 ro)
{
   	float m = ro.y/8.0;
    #ifdef colorize
    return vec4( hash3(floor(ro.xz*0.005*64.0)/64.0)*m,1.0);
    #endif
	vec4 buildings = vec4 (m,m,m,1.0);
	vec4 grass = vec4(0,0.1,0,1);
	return mix(buildings,grass,step(ro.y,0.1));
}
			
vec4 raymarch (vec3 ro, vec3 rd)
{
	for (int i=0; i<256; i++)
	{
		float t = map(ro);
		if ( t<0.001 ) return color(ro);
		ro+=t*rd;
	}
	return vec4(0.0);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = (2.0*fragCoord.xy-iResolution.xy)/iResolution.y;
    vec3 ro = vec3(iTime*10.0,25,0);
    vec3 rd = normalize(vec3(uv,2.0)*rotationX(5.65));
	fragColor = raymarch(ro,rd);
}'''


class StProceduralUrbanLandscape(GlslSource):
    name = 'st_procedural_urban_landscape'
    label = 'Procedural urban landscape'
    description = ''
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.5)
