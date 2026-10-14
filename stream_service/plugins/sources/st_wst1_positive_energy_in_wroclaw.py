"""[WST1]Positive Energy in Wroclaw: Positive Energy in Wroclaw described by Sierpinski triangle. It's my first procjet ;)

Source: https://www.shadertoy.com/view/lllBWn
Author: Ania
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2013 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
// Title: "Wroclaw - my city", Author: Anna Nowacka
// This shader is prepared for the first Wroclaw Shader Competition 
// organized by Faculty of Physics and Astronomy, Khronos Chapter and SIggraph Chapter from Wroclaw



float length2( in vec2 p ) { return dot(p,p); }

const vec2 va = vec2(  0.0, 1.73-0.85 );
const vec2 vb = vec2(  1.0, 0.00-0.85 );
const vec2 vc = vec2( -1.0, 0.00-0.85 );
//const vec3 vd = vec3( 1.0, 0.0, 0.00-0.85 );


    // return distance and address
vec2 map( vec2 p )
{
	float a = 0.0;
	vec2 c;
	float dist, d, t;
	for( int i=0; i<10; i++ )
	{
		d = length2(p-va);                 c = va; dist=d; t=0.0;
        d = length2(p-vb); if (d < dist) { c = vb; dist=d; t=1.0; }
        d = length2(p-vc); if (d < dist) { c = vc; dist=d; t=2.0; }
		p = c + 2.0*(p - c);
		a = t + a*2.5;
	}
	
	return vec2( length(p)/pow(2.0, 7.0), a/pow(3.0,7.0) );
}

float disk(vec2 r, vec2 center, float radius) {
	return 2.0 - smoothstep( radius-0.5, radius+0.5, length(r-center));
}
void mainImage( out vec4 fragColor, in vec2 fragCoord )
    
{
	
    
    vec2 uv = (2.0*fragCoord.xy - iResolution.xy)/iResolution.y;
   
    vec2 r = map( uv*cos(0.3*iTime));
	
	vec3 col = 0.3 + 0.5*sin( 3.1416*r.y +vec3(0.0,5.5,5.0) )+0.4*sin(iTime);
	col *= 1.0 - smoothstep( 0.0, 0.02, r.x )+0.5+0.5*tan(iTime);
	fragColor = vec4( col, 1.0 );
    //vec2 m = vec2(1.5);
    //float an = 3.2 + 0.5*iTime - 6.2831*(m.x-0.5);
      
    if(iTime > 15.0)   
    {vec3 col = 0.3 + 0.5*sin( 3.1416*r.y + vec3(0.0,5.5,5.0) )+0.4*sin(iTime);
	col *= 1.0 - smoothstep( 0.0, 0.02, r.x +0.50*tan(iTime)-0.5);
     fragColor = vec4( col, 1.0 );}
        
    if(iTime > 31.0)
    {vec2 uv = (2.0*fragCoord.xy - iResolution.xy)/iResolution.y;
   
    vec2 r = map( uv*cos(0.3*iTime));
	
	vec3 col = 0.5 + 0.5*sin( 3.1416*r.y + vec3(0.0,5.5,5.0) )+0.4*sin(iTime);
	col *= 1.0 - smoothstep( 0.0, 0.02, r.x )+0.5*sin(iTime);
	fragColor = vec4( col, 1.0 );
    }
    if(iTime > 51.0)
    {vec2 uv = (1.0*fragCoord.xy - iResolution.xy)/iResolution.y;
   
    vec2 r = map( uv*cos(0.3*iTime));
	
	vec3 col = 0.5 + 0.5*sin( 3.1416*r.y + vec3(0.0,5.5,5.0) )+0.4*sin(iTime);
	col *= 1.0 - smoothstep( 0.0, 0.02, r.x )+0.5*(1.00/(cos(iTime)))+1.00;
	fragColor = vec4( col, 1.0 );
    }
    
    if (iTime > 74.0)
    {vec3 col =  1.0 + 0.5*sin( 3.1416*r.y + vec3(0.0,5.5,5.0) )+0.4*sin(iTime);
     col *= 1.0 - smoothstep( 0.0, 0.02, r.x )+0.5*(1.00/(cos(iTime)))+1.00;
	 fragColor = vec4( col, 1.0 );
     
        }
    if (iTime > 89.0)
    {vec3 col =  1.0 + 0.5*sin( 3.1416*r.y + vec3(0.0,5.5,5.0) )+0.4*sin(iTime);
     col *= 1.0 - smoothstep( 0.0, 0.02, r.x )+0.5*(1.00/(cos(iTime)))+1.00;
	 fragColor = floor(vec4( col, 1.0 ));
    }
    if (iTime > 100.0)
    {
    vec2 uv = (2.0*fragCoord.xy - iResolution.xy)/iResolution.y;
   
    vec2 r = map( uv*sin(cos(0.3*iTime)));
	vec3 col =  1.0 + 0.5*tan( 3.1416*r.y + vec3(0.0,5.5,5.0) )+0.5*sin(iTime);
     col *= 1.0 - smoothstep( 0.0, 0.02, r.x )+0.5*tan(iTime)+1.00;
	 fragColor =(log(vec4( col, 2.0 )));
    }
    
    if (iTime > 120.0)
    {
        col = vec3(0.0);
        fragColor = vec4(col, 1.0);
    }

}

'''


class StWst1PositiveEnergyInWroclaw(GlslSource):
    name = 'st_wst1_positive_energy_in_wroclaw'
    label = '[WST1]Positive Energy in Wroclaw'
    description = "Positive Energy in Wroclaw described by Sierpinski triangle. It's my first procjet ;)"
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.5)
