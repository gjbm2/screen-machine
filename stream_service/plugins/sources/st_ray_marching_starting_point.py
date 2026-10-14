"""Ray Marching Starting Point: Raymarching starting point

Source: https://www.shadertoy.com/view/wldyWN
Author: gclkaze
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//Fwtia
//f(y) = 1/(clamp(((1/abs(x+1)*random()*random())+(x*x)),0,6))+1

// "RayMarching starting point" 
// by Martijn Steinrucken aka The Art of Code/BigWings - 2020
// The MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
// Email: countfrolic@gmail.com
// Twitter: @The_ArtOfCode
// YouTube: youtube.com/TheArtOfCodeIsCool
// Facebook: https://www.facebook.com/groups/theartofcode/
//
// You can use this shader as a template for ray marching shaders

#define MAX_STEPS 100
#define MAX_DIST 100.
#define SURF_DIST .001

#define S smoothstep
#define T iTime

mat2 Rot(float a) {
    float s=sin(a), c=cos(a);
    return mat2(c, -s, s, c);
}

float Hash21(vec2 p) {
    p = fract(p*vec2(123.34,233.53));
    p += dot(p, p+23.234);
    return fract(p.x*p.y);
}

float sdBox(vec3 p, vec3 s) {
    p = abs(p)-s;
    return length(max(p, 0.))+min(max(p.x, max(p.y, p.z)), 0.);
    
    
}

float fire(vec3 p){
//f(y) = 1/(clamp(((1/abs(x+1)*random()*random())+(x*x)),0,6))+1
    float r = cos( iTime / 2.);
    float a = (1./abs(p.x+3.)*r);
    float b = p.x * p.x ;
    float c = a + b ;
    float res = clamp(c/.2,0.0,2.) ;
    float inv = ( .1/res );
    return inv;
//float f = 1/(clamp(((1/abs(p.x+1)*r*r)+(p.x*p.x)),0,6))+1;
//    return .1;
}
float GetDist(vec3 p) {
    float d = sdBox(vec3(p.x,p.y - 1.,p.z), vec3(0.3));
    float d2 = sdBox(vec3(p.x,p.y ,p.z), vec3(0.5));
    float d3 = fire(p);
    return min( d3, min(d,d2 ));
}

float RayMarch(vec3 ro, vec3 rd) {
	float dO=0.;
    
    for(int i=0; i<MAX_STEPS; i++) {
    	vec3 p = ro + rd*dO;
        float dS = GetDist(p);
        dO += dS;
        if(dO>MAX_DIST || abs(dS)<SURF_DIST) break;
    }
    
    return dO;
}

vec3 GetNormal(vec3 p) {
	float d = GetDist(p);
    vec2 e = vec2(.001, 0);
    
    vec3 n = d - vec3(
        GetDist(p-e.xyy),
        GetDist(p-e.yxy),
        GetDist(p-e.yyx));
    
    return normalize(n);
}

vec3 GetRayDir(vec2 uv, vec3 p, vec3 l, float z) {
    vec3 f = normalize(l-p),
        r = normalize(cross(vec3(0,1,0), f)),
        u = cross(f,r),
        c = f*z,
        i = c + uv.x*r + uv.y*u,
        d = normalize(i);
    return d;
}



void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
	vec2 m = iMouse.xy/iResolution.xy;
    
    vec3 col = vec3(0);
    
    vec3 ro = vec3(0, 3, -3);
    ro.yz *= Rot(-m.y*3.14+1.);
    ro.xz *= Rot(-m.x*6.2831);
    
    vec3 rd = GetRayDir(uv, ro, vec3(0), 1.);

    float d = RayMarch(ro, rd);
    
    if(d<MAX_DIST) {
    	vec3 p = ro + rd * d;
    	vec3 n = GetNormal(p);
        
    	float dif = dot(n, normalize(vec3(1,2,3)))*.5+.5;
    	col += dif;  
    }
    
    col = pow(col, vec3(.4545));	// gamma correction
    
    fragColor = vec4(col,1.0);
}'''


class StRayMarchingStartingPoint(GlslSource):
    name = 'st_ray_marching_starting_point'
    label = 'Ray Marching Starting Point'
    description = 'Raymarching starting point'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.35)
