"""Ascending onions: No idea how this works, somehow botched transparency into existence and a few artifacts on top of the oniony bits, but looks gd

Source: https://www.shadertoy.com/view/fdV3Wt
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
#define MAX_DIST 100.
#define SURF_DIST .001

#define S smoothstep
#define T iTime

mat2 Rot(float a) {
    float s=sin(a), c=cos(a);
    return mat2(c, -s, s, c);
}

float smin(float a, float b, float k) {
    float h = clamp(.5 + .5*(b-a)/k, 0., 1.);
    return mix(b, a, h) - k * h * (1. - h);
}

float sdBox(vec3 p, vec3 s) {
    p = abs(p)-s;
	return length(max(p, 0.))+min(max(p.x, max(p.y, p.z)), 0.);
}


float GetDist(vec3 p) {
    //float d = sdBox(p, vec3(1));
    float r1 = 2. + cos(p.y + iTime);
    float r2 = 0.2;
    
    float d1 = length(p.xz) - r1;

    float a = atan(p.x,p.z);
    float b = .5 + .5 * cos(iTime);
    
    float cd = length(p.xz) - 0.2
    - abs(pow(cos(0.8 * p.y - iTime),1.5 + .5 * cos(iTime)));
 
    p.y = cos(1. + 3. * cos(3. * a + p.z + p.x + iTime) * cos(3. * p.y));
    float td2 = length(vec2(d1, -0.3 + 0.2 *sin(2. +  p.y - 0.4 * iTime) -0.2 * cos(2. * a + 4. *iTime))) -r2;
    
    // 0.5 *
    td2 += 0.8 * cos(0.4 * iTime) *(0.9 * cos(5. * a + 2. * iTime));
    
    cd -= 0.3 * cos(0.5 * p.y + iTime);
    cd *= 0.5;
    
    return min(cd, td2* 0.5);
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
    vec2 e = vec2(0.001, 0);
    
    vec3 n = d - vec3(
        GetDist(p-e.xyy),
        GetDist(p-e.yxy),
        GetDist(p-e.yyx));
    n *= (n-1.); // i added this
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
    
    vec3 col = mix(vec3(.5,0.,0.),vec3(0.),k);
    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
	vec2 m = iMouse.xy/iResolution.xy;

    vec3 ro = vec3(8. * cos(iTime), 4. + 0.6 * iTime, 8. * sin(iTime));
    //ro.yz *= Rot(-m.y*3.14+1.);
    //ro.xz *= Rot(-m.x*6.2831);
    
    vec3 rd = GetRayDir(uv, ro, vec3(0,-2. + 0.6 * iTime,0), 0.85 + 0.05 * cos(iTime));
    vec3 col = vec3(0);
    col += Bg(rd);
   
    float d = RayMarch(ro, rd);
    d =  RayMarch(ro + rd * 2. * d, -rd);
    if(d<MAX_DIST) {
        vec3 p = ro + rd * d;
        vec3 n = GetNormal(p);
        vec3 r = reflect(rd, n);

        float spec = pow(max(0., r.y),16.);
        float dif = dot(n, normalize(vec3(1,2,3)))*.5+.5;
        dif = 4. * dif * (1.-dif);
        col = vec3(dif);
        float a = atan(p.x,p.z);
        col = mix(Bg(r), vec3(spec), 1.) + 0.2 * spec + Bg(r) * 0.6 *cos(2. * a + 0.1 * p.y + iTime);
      //  col = mix(col, vec3(.5 + .5 * cos(iTime),0.,0.),.1);
    }
    
    
    col = pow(col, vec3(.4545));	// gamma correction
    
    fragColor = vec4(col,1.0);
}'''


class StAscendingOnions(GlslSource):
    name = 'st_ascending_onions'
    label = 'Ascending onions'
    description = 'No idea how this works, somehow botched transparency into existence and a few artifacts on top of the oniony bits, but looks gd'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.45)
