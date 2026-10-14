"""Analytical filter kernel Box Tri: original shader by iq, slightly extended by ollj mouse.y blurs and iMouse.y==0 is nonsensical in a differential context here.

Source: https://www.shadertoy.com/view/lldyzM
Author: ollj
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//self https://www.shadertoy.com/view/lldyzM

//analytical filter kernel,triangular 
//https://www.shadertoy.com/view/llffWs
// Similar to https://www.shadertoy.com/view/XlXBWs,but with a triangular filter kernel,
// which produces less flickering animations that a box filter. Luckily,it's still easily
// http://iquilezles.org/www/articles/morecheckerfiltering/morecheckerfiltering.htm
// checker,2D,box filter: https://www.shadertoy.com/view/XlcSz2
// checker,3D,box filter: https://www.shadertoy.com/view/XlXBWs
// checker,3D,tri filter: https://www.shadertoy.com/view/llffWs
// grid,2D,box filter: https://www.shadertoy.com/view/XtBfzz
// The MIT License
//https://www.shadertoy.com/view/llffWs
// Copyright © 2017 Inigo Quilez
// Permission is hereby granted,free of charge,to any person obtaining a copy of this software and associated documentation files(the "Software"),to dealthe Software without restriction,including without limitation the rights to use,copy,modify,merge,publish,distribute,sublicense,and/or sell copies of the Software,and to permit persons to whom the Software is furnished to do so,subject to the following conditions: The above copyright notice and this permission notice shall be includedall copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",WITHOUT WARRANTY OF ANY KIND,EXPRESS OR IMPLIED,INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,DAMAGES OR OTHER LIABILITY,WHETHERAN ACTION OF CONTRACT,TORT OR OTHERWISE,ARISING FROM,OUT OF ORCONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGSTHE SOFTWARE.


/* ---snippety blog summary esplanation
//fwidth(a,b)=abs(dfdx(p))+abs(dfdy(p))
#define maab(a,b)max(abs(a),abs(b))
the m-parameter is a value,returned from maab(),which may be calculated for multiple textures to be mixed,so it is moved out of the function.

//llffWs is tri-filtering AND box filtering,but it does not need the double integral,but other shaders calculate a double integral.
//there is this double-integral blog post this snioppet is all about:
the basic idea is to smoothen a discontinuity with an antiderivative
"cubic filters" are most common in CG,but this isr "tiangle-filter"ed weights,worse than cubic,better than the box filtering above

f(x)is the square-wave base signal that begs to be filtered.
box-filter formula is integralfromToOf(-w/2,w/2,f(x)dx)
tri-filter formula is integralfromToOf(-w,0,f(x)dx*(w+x)/w)+integralfromtoOf(0,w,f(x)dx*(w-x)/w)

these integrals are integralfromToOf(uv-w/2,uv+w/2,...),but shifting the center simplifies this function.
these integrals are done by [integration by parts],which has lots of symmetries that cancel each other out to:
tri-filter formula is(p(-w*.5)-2.*p(0.)+p(w*.5))/w/w
where p(x)is the antidetivative to f(x)(the striangle wave to the square wave.)
where s(x)is the double-integral of f(x)== the antidefivative of p(x)=an infinite smoothstep function.

//still no double-integrals needed!
vec3 sqrAndIntegrals(float x
){x*=.5;float h=fract(x)-.5,s=-sign(h),t=abs(h)*2.-1.)
 ;return(s,t,x+h*t);}//return vec3(square,tri(integral),smoothsteps(doubleIntegral))
vec2 fTri(vec2 x){vec2 h=fract(x*.5)-.5);return x*.5-h*(abs(h)*2.-1.);}//;return x*.5+h*(1.-2.*abs(h))
float TriFilteredCheckers(vec2 uv,vec2 w//w=maab(dpdx,dpdy)filter kernel
){w+.001
 ;vec2 i=(fTri(uw+w)-2.*fTri(uw)+ftri(uv-w))/(w*w)//analytic integral,3TapFilter function
 ;return .5-.5*i.x-i.y//xor-pattern
 ;}
//still no double-integrals needed!
//anyways,that would be curvature,what use is curvature for surface filtering?
*/


#define scale 5.

// spheres
const vec4 sc0=vec4(2,.5,.8,.5);
const vec4 sc1=vec4(-6,1,-4.,3);
const vec4 sc2=vec4(-16,1,7,4);
const vec4 sc3=vec4(-25,8,0,9);

struct v33{vec3 a;vec3 b;};
//and this ray-transpose function is the strangest of em all to be useful here:
void rayTransp(inout v33 a,inout v33 b){vec3 s=a.b;a.b=b.a;b.a=s;}//swap direction(.b)of [a] with origin(.a)of [b]
v33 sub(v33 a,vec3 b){return v33(a.a-b,a.b-b);}//substract b from all ray components
//component wise ray substraction(this one is a bit odd,differential wise,is basically scaling a rays points)
v33 subc(v33  a,v33 b){return v33(a.a-b.a,a.b-b.b);}//it makes sense in
v33 subc(vec2 a,v33 b){return v33(a.x-b.a,a.x-b.b);}//the context of
v33 subc(v33 a,vec2 b){return v33(a.a-b.x,a.b-b.y);}//v33-differentials for AA
vec2 dt(v33 a,v33 b){return vec2(dot(a.a,b.a),dot(a.b,b.b));}//dual dotprodiuct on v33s
vec2 dt(v33 a,vec3 b){return dt(a,v33(b,b));}
v33 div(v33 a,vec2 b){return v33(a.a/b.x,a.b/b.y);}
v33 mul(v33 a,v33 b){return v33(a.a*b.a,a.b*b.b);}//dual mult
v33 mul(v33 a,vec2 b){return v33(a.a*b.x,a.b*b.y);}
v33 mul(v33 a,float b){return v33(a.a*b,a.b*b);}


float sat(float a){return clamp(a,0.,1.);}
#define dd(a)dot(a,a)
//half-identity-scaling,labeled uN because it scales uv space,usually within a modulo context.
#define u2(a)((a)*2.-1.)
#define u5(a)((a)*.5+.5)
//u3(a)=1.-u2(a)!
//u6(a)=1.-u2(a)!
#define u3(a)(1.-(a)*2.)
#define u6(a)(.5-(a)*.5)
#define maab(a,b)max(abs(a),abs(b))
vec3 maab2(v33 a){return maab(a.a,a.b);}
float suv(vec3 a){return a.x+a.y+a.z;}
float prv(vec3 a){return a.x*a.y*a.z;}
float miv(vec2 a){return min(a.x,a.y);}
float miv(vec4 a){return min(miv(a.xy),miv(a.zw));}
float ss01(float a){return smoothstep(0.,1.,a);}

// ---unfiltered checkerboard ---
#define checker(a)mod(suv(floor(a)),2.)
//analytically triangle-filtered checkerboard:  https://www.shadertoy.com/view/MtffWs
#define Fa(a,b)u2(abs(b-.5))
#define Fb(a,b)((a)*.5-((b)-.5)*Fa(a,b))
#define tri(a,b)b(a,fract((a)*.5))
//noe to self,maybe replace iMouse.y by abs(angleBetween(rayDirection,Normal))/quaterRotation
//tri(a,Fa)2xTap for box-filtering,used a lot in CG
float checkerF2(vec3 p,vec3 w){w+=iMouse.y/iResolution.y//filter kernel increase this value over inverse squared distance?
 ;return u6(prv((tri(p-.5*w,Fa)-tri(p+.5*w,Fa))/w));}//analytical integral(box filter),xor pattern
//tri(a,Fb)3xTap for tri-filtering,is slightly better than checkerF2()
float checkerF3(vec3 p,vec3 w){w+=iMouse.y/iResolution.y//filter kernel increase this value over inverse squared distance?
 ;return u6(prv((tri(p+w,Fb)-2.*tri(p,Fb)+tri(p-w,Fb))/(w*w)));}// analytical integral(tri filter),xor pattern

//sphere softShadow of(ray,sphere)
float sssp(v33 r,vec4 s){vec3 oc=s.xyz-r.a;float b=dot(oc,r.b),z=1.;if(b>0.){float h=dd(oc)-b*b-s.w*s.w;z=ss01(2.*h/b);}return z;}
//sphere occlusion
float occSphere(vec3 u,vec3 n,vec4 s){vec3 i=s.xyz-u;return 1.-dot(n,normalize(i))*s.w*s.w/dd(i);}

float iSphere(v33 r,vec4 s){vec3 e=r.a-s.xyz;float b=dot(r.b,e),h=b*b-dd(e)+s.w*s.w,t=-1.;if(h>0.)t=-b-sqrt(h);return t;}

float intersect(vec3 ro,vec3 rd,out vec3 pos,out vec3 n,out float occ,out float matid
){
 ;mat4 sc=mat4(sc0,sc1,sc2,sc3)
 ;float tmin=10000.
 ;n=vec3(0)
 ;occ=1.
 ;pos=vec3(0)
 ;float h=(.01-ro.y)/rd.y//plane
 ;if(h>0.
){tmin=h
  ;n=vec3(0,1,0)
  ;pos=ro+h*rd
  ;matid=0.
  ;occ=occSphere(pos,n,sc[0])*occSphere(pos,n,sc[1])*occSphere(pos,n,sc[2])*occSphere(pos,n,sc[3])
  ;}
 ;for(int i=0;i<4;i++){
  ;float h=iSphere(v33(ro,rd),sc[i])
  ;bool b=abs(h-.5*tmin)<tmin*.5//==h>0.&&h<tmin
  ;if(b){tmin=h;pos=ro+tmin*rd;n=normalize(ro+h*rd-sc[i].xyz);matid=1.;occ=u5(n.y);}}
 ;return tmin;}

void calcCamera(out vec3 ro,out vec3 ta){float an=.3*sin(.04*iTime);ro=vec3(5.5*cos(an),1.,5.5*sin(an));ta=vec3(0,1,0);}

vec3 doLighting(vec3 pos,vec3 rd,vec3 n,float occ
){ ;v33 rrr=v33(pos,rd)//seems to be a shared light source position
 ;float sh=miv(vec4(sssp(rrr,sc0),sssp(rrr,sc1),sssp(rrr,sc2),sssp(rrr,sc3)))
 ,dif=sat(dot(n,vec3(.57703)));float bac=sat(dot(n,vec3(-.707,.0,-.707)))
 ;vec3 lin=dif*sh*vec3(1.5,1.4,1.3)
 ;lin+=occ*vec3(.15,.2,.3);lin+=bac*vec3(.1);return lin;}

v33 calcRayForPixel(vec2 pix,vec2 res
){vec2 p=(-res.xy+2.0*pix)/res.y
 ;vec3 ro,ta
 ;calcCamera(ro,ta)
 ;vec3 w=normalize(ta-ro)
 ;vec3 u=normalize(cross(w,vec3(0,1,0)))
 ;vec3 rd=normalize(p.x*u+p.y*normalize(cross(u,w))+1.5*w)
 ;return v33(ro,rd);}

void mainImage(out vec4 fragColor,vec2 fragCoord
){vec2 res=vec2(iResolution.x/3.0,iResolution.y)
 ;int id=int(floor(fragCoord.x/res.x))
 ;vec2 px=vec2(fragCoord.x-float(id)*res.x,fragCoord.y)
 ;v33 r0=calcRayForPixel(px+vec2(0,0),res)
 ;vec3 pos,nor
 ;float occ,mid;float t=intersect(r0.a,r0.b,pos,nor,occ,mid)
 ;vec3 col=vec3(.9)
 ;if(t<100.
 //todo,measure angle between normal and rayDirection,and only do  #if 1 for anggles>45deg;
 //todo,there is a precision fix for near-orthogonal normals to camera that may be good here.
  #if 1
 ){vec3 uvw=pos*scale//analytic ray-differential is in object-space
   ;v33 rx=calcRayForPixel(px+vec2(1,0),res);
   ;v33 ry=calcRayForPixel(px+vec2(0,1),res);
   ;rayTransp(rx,ry)//swap rx.b with ry.a and the lines below become more symmetric: yes,this swaps the origin of one ray with the direction of another.
   ;v33 w=mul(ry,dt(sub(rx,pos),nor)/dt(ry,nor))
   ;w=subc(rx,w)
   ;w=mul(sub(w,pos),scale)
 #else
 ){vec3 uvw=pos*scale;v33 w=v33(dFdx(uvw),dFdy(uvw))//semi-analogously use dFdx()dFdy()in screenspace has bad borders
 #endif
  ;vec3 m=vec3(0)
  ;w.a=maab2(w)
  ;if(id==0)m=vec3(1)*checker(uvw)
  ;else if(id==1)m=vec3(1)*checkerF2(uvw,w.a)
  ;else if(id==2)m=vec3(1)*checkerF3(uvw,w.a)
  ;col=m*doLighting(pos,vec3(.57703),nor,occ)
  //;col=mix(col,vec3(.9),1.-exp(-.0001*t*t))// fog  
 ;}
 ;col=pow(col,vec3(.4545))//gamma
 ;col*=smoothstep(2.,3.,abs(px.x))//frame border lines
 ;fragColor=vec4(col,1);}
 '''


class StAnalyticalFilterKernelBoxTri(GlslSource):
    name = 'st_analytical_filter_kernel_box_tri'
    label = 'Analytical filter kernel Box Tri'
    description = 'original shader by iq, slightly extended by ollj mouse.y blurs and iMouse.y==0 is nonsensical in a differential context here.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
