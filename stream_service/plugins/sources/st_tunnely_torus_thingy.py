"""Tunnely Torus Thingy: Super happy with this one, backgrounds a bit empty. code is sloppy and not much color variation but i like it

Source: https://www.shadertoy.com/view/7dKGWK
Author: SnoopethDuckDuck
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// "RayMarching starting point" 
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
#define MAX_DIST 350.
#define SURF_DIST .001

#define S smoothstep
#define T iTime

mat2 Rot(float a) {
    float s=sin(a), c=cos(a);
    return mat2(c, -s, s, c);
}

float sdBox(vec3 p, vec3 s) {
    p = abs(p)-s;
	return length(max(p, 0.))+min(max(p.x, max(p.y, p.z)), 0.);
}

float sdSphere(vec3 p, float r) {
    return length(p) - r;
}

float GetDist(vec3 p) {
    float d = sdBox(p, vec3(1));
    float r1 = min(20.,1.5 * iTime + 1.5);////iTime+ 0. + 0.1 * p.z, r2 = 0.3; //1.5
    float r2 = 0.3;
    vec2 cp = vec2(length(p.xy) - r1, p.z);
    float a = atan(p.x,p.y);
    cp.x = abs(cp.x) - 1.4 -4.* cos(0.8 * min(iTime,(30./4.) * 3.1415));
    cp.x += cos(2. * a + 8. * iTime +  p.z) - 2.;
    cp.y = 0.4;//cos(p.z + iTime) + sin(p.z - iTime);
    cp *= Rot(9. * a + 1.4 *iTime );
    cp.x = abs(cp.x)- 0.3 * abs(cp.y + min(1.8, 0.02 * p.z));// + 3.5 * cos(1.2 * p.z));
    float td = length(cp)- r2;
    td *= 0.5;
    
    float sd = sdSphere(p - vec3(2. * sin(1.8 * iTime),2. * cos(2. * iTime),-21. + 2. * iTime), 2.4 + 2. * sin(iTime));
    //float bd = sdBox(p - vec3(0,0,-21.+2. * iTime), vec3(5.));
    //d = min(td, p.z + 0.9 * iTime);
    return min(sd,td);
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

vec3 Bg(vec3 rd) {
    
    float b1 = .5 + .5 * cos(iTime + (2./3.) * 3.1415);
    float b2 = .5 + .5 * cos(iTime + (4./3.) * 3.1415);
    float b3 = .5 + .5 * cos(iTime);
    
    float k = (b1 * rd.x + b2 * rd.y + b3 * rd.z) + .5;
    //k = clamp(k,0.,1.);
    vec3 col = mix(vec3(95,205,228)/255., vec3(0), k * (1.-k));
    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
	vec2 m = iMouse.xy/iResolution.xy;

    float s = 2.;
    vec3 ro = vec3(s * cos(1.2 * iTime),s * sin(1.2 * iTime), 1. + 2. * iTime);
    //vec3 ro = vec3(0.,0.,1. + 2. * iTime);
   //  vec3 ro = vec3(0,0, 1. + iTime + cos(iTime));
   // ro.yz *= Rot(-m.y*3.14+1.);
    //ro.xz *= Rot(-m.x*6.2831);
    
    vec3 rd = GetRayDir(uv, ro, vec3(0,0.,0.), 1.);
    vec3 col = vec3(0);
    col += Bg(rd);
   
    float d = RayMarch(ro, rd);

    if(d<MAX_DIST) {
        vec3 p = ro + rd * d;
        vec3 n = GetNormal(p);
        vec3 r = reflect(rd, n);
        float a = atan(r.z, r.y);
        vec2 r2 = r.xy * Rot( iTime);
        vec2 r3 = r.xy * Rot((iTime + (2./3.) * 3.1415));
        vec2 r4 = r.xy * Rot((iTime + (4./3.) * 3.1415));
        
        float spec2 = pow(max(0., r2.x ),32.);
        float spec3 = pow(max(0., r3.x ),32.);
        float spec4 = pow(max(0., r4.x ),32.);
        
        float dif = dot(n, normalize(vec3(0,0,0)))*.5+.5;
        col = mix(Bg(r), vec3(dif), 0.5);
        col += spec2 + spec3 + spec4;
        
        //col +=0.2 *( 1.- 16. * col * col * (1.-col) * (1.-col));// + spec;
    }
    
   // col = pow(col, vec3(.4545));	// gamma correction
    
    fragColor = vec4(col,1.0);
}'''


class StTunnelyTorusThingy(GlslSource):
    name = 'st_tunnely_torus_thingy'
    label = 'Tunnely Torus Thingy'
    description = 'Super happy with this one, backgrounds a bit empty. code is sloppy and not much color variation but i like it'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
