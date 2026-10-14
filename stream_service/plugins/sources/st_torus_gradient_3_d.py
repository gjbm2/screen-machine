"""Torus   gradient 3 D: Computes the analytic gradient/normal of a torus SDF, which is much faster than doing it numerically (useful when the normal is needed inside the SDF...

Source: https://www.shadertoy.com/view/wtcfzM
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

// sdgTorus() returns the torus SDF and its gradient, by 
// computing it analytically. This means the normal to
// the torus surface can be used during the raymarch loop
// rather inexpensivelly (compared to sampling the SDF
// multiple times to evaluate a normal for it)

// Other SDF analytic gradients:
//
// Torus:   https://www.shadertoy.com/view/wtcfzM
// Capsule: https://www.shadertoy.com/view/WttfR7


// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .w = ∂f(p)/∂z
// .yzw = ∇f(p) with ‖∇f(p)‖ = 1
vec4 sdgTorus( vec3 p, float ra, float rb )
{
    float h = length(p.xz);
    return vec4( length(vec2(h-ra,p.y))-rb,
                 normalize(p*vec3(h-ra,h,h-ra)) );
}


#define AA 3

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
     // camera movement	
	float an = 0.5*(iTime-10.0);
	vec3 ro = 1.2*vec3( 1.0*cos(an), 0.65, 1.0*sin(an) );
    vec3 ta = vec3( 0.0, -0.15, 0.0 );
    // camera matrix
    vec3 ww = normalize( ta - ro );
    vec3 uu = normalize( cross(ww,vec3(0.0,1.0,0.0) ) );
    vec3 vv = normalize( cross(uu,ww));
   
    // animate torus
    float ra = 0.5;
    float rb = 0.2+0.1*sin(iTime);
    
    // render    
    vec3 tot = vec3(0.0);
    #if AA>1
    for( int m=0; m<AA; m++ )
    for( int n=0; n<AA; n++ )
    {
        // pixel coordinates
        vec2 o = vec2(float(m),float(n)) / float(AA) - 0.5;
        vec2 p = (2.0*(fragCoord+o)-iResolution.xy)/iResolution.y;
        #else    
        vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
        #endif

	    // create view ray
        vec3 rd = normalize( p.x*uu + p.y*vv + 1.5*ww );

        // raymarch
        const float tmax = 5.0;
        float t = 0.0;
        for( int i=0; i<256; i++ )
        {
            vec3 pos = ro + t*rd;
            float h = sdgTorus(pos,ra,rb).x;
            if( h<0.0001 || t>tmax ) break;
            t += h;
        }
        
    
        // shading/lighting	
        vec3 col = vec3(0.0);
        if( t<tmax )
        {
            vec3 pos = ro + t*rd;
            vec3 nor = sdgTorus(pos,ra,rb).yzw;

            // compute normal numerically, for comparison
            // http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
            #if 0
            const vec2 e = vec2(1,-1);
            const float eps = 0.0002;
            nor = normalize( e.xyy*sdgTorus( pos + e.xyy*eps, ra, rb ).x + 
                             e.yyx*sdgTorus( pos + e.yyx*eps, ra, rb ).x + 
                             e.yxy*sdgTorus( pos + e.yxy*eps, ra, rb ).x + 
                             e.xxx*sdgTorus( pos + e.xxx*eps, ra, rb ).x );

            #endif

            float dif = clamp( dot(nor,vec3(0.57703)), 0.0, 1.0 );
            float amb = 0.5 + 0.5*dot(nor,vec3(0.0,1.0,0.0));
            col = vec3(0.2,0.3,0.4)*amb + vec3(0.85,0.75,0.65)*dif;
            col *= (0.5+0.5*nor)*(0.5+0.5*nor);
        }

        // gamma        
        col = sqrt( col );
	    tot += col;
    #if AA>1
    }
    tot /= float(AA*AA);
    #endif

	fragColor = vec4( tot, 1.0 );
}'''


class StTorusGradient3D(GlslSource):
    name = 'st_torus_gradient_3_d'
    label = 'Torus   gradient 3 D'
    description = 'Computes the analytic gradient/normal of a torus SDF, which is much faster than doing it numerically (useful when the normal is needed inside the SDF...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
