"""Breaking raymarching: messing around trying to break things

Source: https://www.shadertoy.com/view/ss3XWN
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

#define MAX_STEPS 50
#define MAX_DIST 5.
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


float GetDist(vec3 p) {
    float a = atan(p.z, p.x);
   // float b = .5 + .5 * cos(0.3 * iTime);
    p = vec3(p.x,p.y +0.1 *  length(p.xz) * cos(6. * a + 4. * iTime),p.z);
    float bd = sdBox(p, vec3(0.8)) - .2;
    float sd = length(p) * length(p) - 1.2;// + 0.4 * cos(0.7 * iTime);
    //return  0.2 * (0.8 + sin(16.* (0.2 * min(bd,sd) + (1. - abs(min(sd/(bd),bd/(sd)))))));
    return  -0. * min(bd,sd) + 0.05 * (2. + sin(0.5 * iTime + 1. * (0.6 - abs(min(sd/(bd),bd/(sd))))));
   
   
   //return bd;
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
    float k = rd.y*.5 + .5;
    //  vec3 col = mix(vec3(.5,0.,0.),vec3(0.),16. * k * (1.-k) * k * (1.-k));
   // vec3 col = vec3(10.,0.,0.);
    vec3 col = mix(vec3(.5,0.,0.),vec3(0.),16. * k * (1.-k) * k * (1.-k));
    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
	vec2 m = iMouse.xy/iResolution.xy;
    float t = 0.35 * iTime;
    vec3 ro = 2.5 * vec3(cos(t), cos(t + (2./3.) * 3.1415), cos(t + (4./3.) * 3.1415));
   // vec3 ro = vec3(3.5 * cos(0.2 * iTime),2. * sin(0.5 * iTime), 3.5 * sin(0.2 * iTime));
    // ro.yz *= Rot(-m.y*3.14+1.);
    //ro.xz *= Rot(-m.x*6.2831);
    
    vec3 rd = GetRayDir(uv, ro, vec3(0,0.,0), 1.);
    vec3 col = vec3(0);
    col += Bg(rd);
   
    float d = RayMarch(ro, rd);
    float depth = 2.; //1.5 + cos(iTime);
    
    // comment / uncomment me
    //d = RayMarch(ro + rd * (1. + depth) * d, -0.5 * depth * rd);
    
    if(d<MAX_DIST) {
        vec3 p = ro + rd * d;
        vec3 n = GetNormal(p);
        vec3 r = reflect(1.5*rd, n);
        
        float spec = pow(max(0., -r.y),32.);
        spec = .5 + .5 * cos(0.00001*spec); // <-- absolute fudge but works alright
       
       float dif = dot(n, normalize(vec3(1,2,3)))*.5+.5;
       float a = atan(p.x,p.z);
       // dif = 16. * dif * dif * (1.-dif) * (1.-dif);
       
       col = 0.4 * vec3(dif) + 1.5 * cos(2. * a) * Bg(r);        
       col *= (1. + spec);
       
       // comment / uncomment me
       //col = vec3(dif);
    }
    col = pow(col, vec3(.4545));	// gamma correction
    //col *= 100000.;
    fragColor = vec4(col,1.0);
}'''


class StBreakingRaymarching(GlslSource):
    name = 'st_breaking_raymarching'
    label = 'Breaking raymarching'
    description = 'messing around trying to break things'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
