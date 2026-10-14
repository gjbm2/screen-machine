"""Oval   gradient 3 D: Oval with analytic gradient Oval shape is from sylvain69780 shader here https://www.shadertoy.com/view/Ws3BWH, but 3D and modified to be branchless so there...

Source: https://www.shadertoy.com/view/NdsSRM
Author: Dain
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

//Rendering code from IQs torus gradient shader https://www.shadertoy.com/view/wtcfzM

// sdgOval returns the oval SDF and its gradient, by 
// computing it analytically. 

// Other SDF analytic gradients
// Egg: https://www.shadertoy.com/view/7dXSz7
// Oval: https://www.shadertoy.com/view/NdsSRM
// Disk: https://www.shadertoy.com/view/NdlSR7
// Box : https://www.shadertoy.com/view/NslSz7


// Other SDF analytic gradients(By IQ):
//
// Torus:   https://www.shadertoy.com/view/wtcfzM
// Capsule: https://www.shadertoy.com/view/WttfR7

//Set to 1 to show the finite difference gradient for comparison
#define SHOW_NUMERIC_GRADIENT 0

//A Z up oval, similiar to a capsule with a customizable mid radius
// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .w = ∂f(p)/∂z
// .yzw = ∇f(p) with ‖∇f(p)‖ = 1
vec4 sdgOvalZ(vec3 pIn, float  a, float b, float h) {
    
    //These first 4 lines can be precalculated once
    float r = a - b; //a must be greater than b!
	float l = (h * h - r * r) / (r+r);
	float sub2 = (a + l);
	float sub1 = sub2 - length(vec2(h, l));
       

    vec2 p = vec2(length(pIn.xy), abs(pIn.z) );
    
	bool isTop =((p.y-h)*l) > p.x * h;
    
	float y = isTop? h: 0.0;
	float x = isTop ? 0.0: l;

	vec2 p2 = vec2( p.x + x, p.y - y );
   
	float d = length(p2)- (isTop ? sub1 : sub2);  
	vec3 grad = vec3(pIn.xy, pIn.z-y)*vec3(p2.x, p2.x, p.x );
  
	return vec4(d, normalize(grad));
}

//This shader assumes Y is up, so wrapping it to call the Z up oval
vec4 sdgOvalY(vec3 p, float  a, float b, float h) {
    p.xyz = p.xzy;
    vec4 r= sdgOvalZ(p, a,b,h);
    r.yzw = r.ywz;
    return r;
}

#define AA 3

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
     // camera movement	
	float an = 0.5*(iTime-10.0);
	vec3 ro = 1.2*vec3( 1.0*cos(an),1.30, 1.0*sin(an));
    vec3 ta = vec3( 0.0, .5, 0.0 );
    // camera matrix
    vec3 ww = normalize( ta - ro );
    vec3 uu = normalize( cross(ww,vec3(0.0,1.0,0.0) ) );
    vec3 vv = normalize( cross(uu,ww));
   
    // animate torus
    float ra = 0.5 + 0.4*cos(iTime);
    float rb = min(0.1+0.1*(sin(iTime)), ra*.9);
    float height = abs(cos(iTime*.5))*.5 +0.6;
    
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
            float h = sdgOvalY(pos,ra,rb, height).x;
            if( h<0.0001 || t>tmax ) break;
            t += h;
        }
        
    
        // shading/lighting	
        vec3 col = vec3(0.0);
        if( t<tmax )
        {
            vec3 pos = ro + t*rd;
            vec3 nor = sdgOvalY(pos,ra,rb, height).yzw;

            // compute normal numerically, for comparison
            // http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
            #if SHOW_NUMERIC_GRADIENT
            const vec2 e = vec2(1,-1);
            const float eps = 0.0002;
            nor = normalize( e.xyy*sdgOvalY( pos + e.xyy*eps, ra, rb,height ).x + 
                             e.yyx*sdgOvalY( pos + e.yyx*eps, ra, rb,height ).x + 
                             e.yxy*sdgOvalY( pos + e.yxy*eps, ra, rb,height ).x + 
                             e.xxx*sdgOvalY( pos + e.xxx*eps, ra, rb,height ).x );

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


class StOvalGradient3D(GlslSource):
    name = 'st_oval_gradient_3_d'
    label = 'Oval   gradient 3 D'
    description = 'Oval with analytic gradient Oval shape is from sylvain69780 shader here https://www.shadertoy.com/view/Ws3BWH, but 3D and modified to be branchless so there...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
