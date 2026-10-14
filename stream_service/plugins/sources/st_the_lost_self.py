"""The Lost Self: a sense of self-lost in the gyroid shape.

Source: https://www.shadertoy.com/view/3t3Bzs
Author: YitingLiu
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

float sdBox(vec3 p, vec3 s) {
    p = abs(p)-s;
	return length(max(p, 0.))+min(max(p.x, max(p.y, p.z)), 0.);
}

float sdGyroid(vec3 p,float scale, float thickness, float bias){
    p*=scale;
    
    // can also play with the p and p.zxy by adding some numbers 
    // scale needs to add the largest multiplers in either p or p.zxy 
    // return abs(dot(sin(p*2.),cos(p.zxy*1.23))-bias)/(scale*2.)-thickness;

    return abs(dot(sin(p),cos(p.zxy))-bias)/scale-thickness;
    
    }

float GetDist(vec3 p) {
    float box = sdBox(p, vec3(1));
    
    // wanting something organic? the scales for two should not be multiplicable by each other (ex.: can't be 4 and 8)
    float g1 = sdGyroid(p,5.,.02,1.5);
    float g2 = sdGyroid(p+vec3(1.),8.85,.02,2.);
    float g3 = sdGyroid(p+vec3(2.),15.85,.03,0.32);
    float g4 = sdGyroid(p+vec3(3.),28.85,.04,1.2);
    float g5 = sdGyroid(p+vec3(3.),34.20,.05,1.3);
    
    //float g = max(g1,g2);//union 
  //float g = max(g1,-g2);//subtraction 
    
    g1-=g2*.3;//bump mapper 
    g1-=g3*.2;//bump mapper 
    g1+=g4*.1;//bump mapper 
    
    //float d=max(box,g1*.8);// intersect the box 
   	
    float d = g1*.8;
    return d;
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
    
    float fft  = texture( iChannel0, vec2(uv.x-1.)).x; 
	
    // second texture row is the sound wave
	float wave = texture( iChannel0, vec2(uv.x-1.,0.1) ).x;
	
	// convert frequency to colors
	vec3 soundEl = vec3(fft,0.5*fft*(1.0-fft),fft );// * fft;

    vec3 col = vec3(0);
    
    vec3 ro = vec3(0, 3, -3)*S(-.01,0.01,fft)*0.5;
    ro.yz *= Rot(abs(sin(iTime*.2))*S(-.01,0.028,fft)*3.14+1.);
    ro.xz *= Rot(cos(iTime*.5*S(-.01,0.01,fft))*6.2831);
    
    // mouse controls 3d 
    //ro.yz *= Rot(-m.y*3.14+1.);
    //ro.xz *= Rot(-m.x*6.2831);
    
    //zoom factor 
    vec3 rd = GetRayDir(uv, ro, vec3(0),2.*sin(iTime*0.5*S(-0.01,0.1,fft)));// S(0.,0.2,fft)*5.

    float d = RayMarch(ro, rd);
    
    if(d<MAX_DIST) {
    	vec3 p = ro + rd * d;
    	vec3 n = GetNormal(p);
        
    	float dif = dot(n, normalize(vec3(1,2,3)))*.5+.5;
    	col+=n*.5+.5;// 0 <> 1 
        col -= dif*soundEl;  
    
        //ambient occulusion - cheaper 
    float g2 = sdGyroid(p+vec3(1.),8.85,.02,2.);
        col*=S(-.1,.1,g2)-soundEl;

    }
    
   // col*=0.;// for 2d
    d = sdGyroid(vec3(uv.x,uv.y,iTime*.1),10.,.02,.3);
   //col+=d*.8; // for 2d 
    
    col = pow(col, vec3(.4545));	// gamma correction
    
    fragColor = vec4(col,1.0);
}'''


class StTheLostSelf(GlslSource):
    name = 'st_the_lost_self'
    label = 'The Lost Self'
    description = 'a sense of self-lost in the gyroid shape.'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.5)
