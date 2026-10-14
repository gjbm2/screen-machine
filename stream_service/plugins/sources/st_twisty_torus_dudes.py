"""Twisty Torus Dudes: Following along with the art of code's torus video and made this, the colors are a bit off but I like it

Source: https://www.shadertoy.com/view/7dV3Dy
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

float Hash21(vec2 p) {
    p = fract(p*vec2(123.34,233.53));
    p += dot(p, p+23.234);
    return fract(p.x*p.y);
}

float myAbs(float k) {
    return (1. - exp2(-4. * abs(k))) * abs(k);
}


float sdSphere(vec3 p, float r) {
   
    float a =atan(p.x,p.z);
   // p.xz *= Rot(3.1415 * cos(a + iTime));
    p.x += 0.5 * cos(3. * a + 0.7 * iTime + myAbs(p.y) - 0.4);
    p.z += 0.5 * sin(1.*a + iTime + myAbs(p.y) - 0.4);
    //p.z += 0.5 * sin(2. * a + iTime);
    p.y *= 0.;// + 0.01 * Hash21(100. * p.xz);
    float d = myAbs(p.x) + myAbs(p.y) +  myAbs(p.z) - r;
    return 0.35 * d;
}

float sdBox(vec3 p, vec3 s) {
    p = abs(p)-s;
	return length(max(p, 0.))+min(max(p.x, max(p.y, p.z)), 0.);
}

float sharpFunc(float x) {
    return 1.-abs(cos(x));
}

float GetDist(vec3 p) {
    float r1 = 2.;
    float r2 = 0.08;
    vec2 cp = vec2(length(p.xz) - r1, p.y);
    float a = atan(p.x,p.z);
    cp *= Rot(1.5 * a);
    cp.x = abs(cp.x) - 0.4 - 0.4 * abs(cp.y);
    cp.y = abs(cp.y) - 0.4 ;
    vec3 e = vec3(1. + cos(10. * a + 4. * iTime), 0., 1. + sin(10. * a + 4. * iTime));
  //  float d = length(cp) + .05 * smoothstep(0.,-1.,cos(10. * a + 4. * iTime)) *length(p + e) - r2;
    float td = length(cp) + 
              .5 * sharpFunc(.5 * a + 3.1415 * cos(0.5 * a + iTime)) * length( p ) 
              - r2;
    td *= 0.5;
    //float bd = sdBox(p - vec3(0), vec3(0.5));
    float sd = sdSphere(p, 0.8); //1.2
    return min(td,sd);
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
    float k = rd.y * .5 + .5;
    float b1 = .5 + .5 * cos(iTime + (2./3.) * 3.1415);
    float b2 = .5 + .5 * cos(iTime + (4./3.) * 3.1415);
    float b3 = .5 + .5 * cos(iTime);
    
    
    vec3 col = mix(vec3(b1,b2,b3), vec3(0.01 * Hash21(rd.xz)), k);
    return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
	vec2 m = iMouse.xy/iResolution.xy;
    
    vec3 col = vec3(0);
    
    float t = -0.2 * iTime;
    vec3 ro = vec3(5. * cos(t), 6. * cos(0.6 * iTime), 5. * sin(t));
    //ro.yz *= Rot(-m.y*3.14+1.);
    //ro.xz *= Rot(-m.x*6.2831);
    
    vec3 rd = GetRayDir(uv, ro, vec3(0,0.,0), 1.);
     col += Bg(rd);
   
    float d = RayMarch(ro, rd);

    if(d<MAX_DIST) {
        vec3 p = ro + rd * d;
        vec3 n = GetNormal(p);
        vec3 r = reflect(rd, n);

        float spec = pow(max(0., r.y), 24.);
        float dif = dot(n, normalize(vec3(1,2,3)))*.5+.5;
        col = mix(Bg(r), vec3(dif), 0.5);
        col = 1.- 16. * col * col * (1.-col) * (1.-col) + spec;
       // col = vec3(spec);
    }
    
    col = pow(col, vec3(.4545));	// gamma correction
    
    fragColor = vec4(col,1.0);
}'''


class StTwistyTorusDudes(GlslSource):
    name = 'st_twisty_torus_dudes'
    label = 'Twisty Torus Dudes'
    description = "Following along with the art of code's torus video and made this, the colors are a bit off but I like it"
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
