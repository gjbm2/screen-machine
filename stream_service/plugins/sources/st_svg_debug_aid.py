"""SVG debug aid: Showing not one but all corner problem areas in Bézier spline distance fields in [url=https://www.shadertoy.com/view/MlVSWc]SVG viewer[/url] by FabriceNeyret2

Source: https://www.shadertoy.com/view/4lySWd
Author: TimoKinnunen
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Permission is hereby granted, free of charge, to any person obtaining a copy of this so
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''////////////////////////////
//
// SVG Distance field discontinuities
//
// Adapting svg viewer / nVidia logo by FabriceNeyret2
// @ https://www.shadertoy.com/view/MlVSWc
//
// And utilizing distance meter by cupe
// @ https://www.shadertoy.com/view/ldK3zD

//#define SHOW_GLITCH
#define IMPROVE_ACCURACY
#define REPLACE_ZERO_DISTANCE

// splines discretization. Lower it on slow GPUs
const float N = 20.;
vec2 uv;
vec2 interpolate(vec2 G1, vec2 G2, vec2 G3, vec2 G4, float t);
void pnpolyAdd(vec2 p, vec2 a, vec2 b);
float fOpUnion(float a, float b);
float fLineSegment(vec2 p, vec2 a, vec2 b);

void bezier(vec2 A, vec2 B, vec2 C, vec2 D) {
	vec2 p = A;
#ifdef SHOW_GLITCH
		for(float t = 1.; t<= N; t++) {
			// The expression t/N here is problematic.
			// As division is expensive, the compiler
			// is allowed to instead do it as t*(1./N).
			// (1./N) for N=20 is about 0.050, or more
			// precisely 0.0500000007450580596923828125.
			// Multiplied by 20, before rounding that's
			// 1.00000001490116119384765625 which the
			// compiler can round freely to 1.0 or
			// something else. A gap between edges would
			// then be the result. This isn't the cause
			// of the glitch in this case, however...
			vec2 q = interpolate(A,B,C,D,t/ N);
			pnpolyAdd(uv,p,q);
			p = q;
		}
#else
		for(float t = 1.; t< N; t++) {
			vec2 q = interpolate(A,B,C,D,t/ N);
			pnpolyAdd(uv,p,q);
			p = q;
		}
		pnpolyAdd(uv,p,D);
#endif
}

// --- spline interpolation ( inspired from revers https://www.shadertoy.com/view/MlGSz3 )
vec2 interpolate(vec2 G1, vec2 G2, vec2 G3, vec2 G4, float t) {
	vec2 A = G4- G1+ 3.* (G2- G3),B = 3.* (G1- 2.* G2+ G3),C = 3.* (G2- G1),D = G1;
	// When t=1, we would expect to get G4 from this
	// and mathematically speaking that's what we'd
	// get too as everything else cancels out. But
	// the route leading to G4 is not so simple and
	// straight-forward. For G4 to be the result,
	// G4-G1 needs to be calculated without causing
	// round-off. In addition, an expression like
	// 3.*(G2-G1) is problematic, as multiplying
	// something by 3 requires something to have 1
	// unused mantissa bit or else round-off will
	// again occur.
	return t* (t* (t* A+ B)+ C)+ D;
}

// constants and stuff

#define int1 int
#define float1 float
#define mul( a , b ) (a) * (b)
#define div( a , b ) (a) / (b)
#define add( a , b ) (a) + (b)
#define sub( a , b ) (a) - (b)

// === SVG Player ====      short spec: https://www.w3.org/TR/2008/REC-SVGTiny12-20081222/paths.html

const float PI = 3.1415926535897932384626433832795;
const float INFINITY = 1e38;
const float FILL = 1.,CONTOUR = 0.,DIR = 1.,INDIR = -1.;
float _d = INFINITY;
float _x,_y,x0,y0;

vec4 COL = vec4(0);
float fill = FILL,dir = DIR,contrast = 1.;  // style state

float saturate(float x) {
	return clamp(x,0.,1.);
}
float fOpUnion(float a, float b) {
	return min(a,b);
}
// Distance to line segment between a and b
float fLineSegment(vec2 p, vec2 a, vec2 b) {
	vec2 ab = b- a;
	float t = saturate(dot(p- a,ab)/ dot(ab,ab));
	return length((ab* t+ a)- p);
}
//rotate p.x towards p.y by a radians
vec2 pR(vec2 p, float a) {
	return cos(a)* p+ sin(a)* vec2(p.y,-p.x);
}
// The original version of _pnpoly_ is available @
// https://www.ecse.rpi.edu/Homepages/wrf/Research/Short_Notes/pnpoly.html or @
// http://web.archive.org/web/20161116163747/https://www.ecse.rpi.edu/Homepages/wrf/Research/Short_Notes/pnpoly.html
//
// Copyright (c) 1970-2003, Wm. Randolph Franklin
//
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
//
// Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimers.
// Redistributions in binary form must reproduce the above copyright notice in the documentation and/or other materials provided with the distribution.
// The name of W. Randolph Franklin may not be used to endorse or promote products derived from this Software without specific prior written permission.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
//
bool pInPoly = false;
float pInPolyDist = INFINITY;
void pnpolyAdd(vec2 p, vec2 a, vec2 b) {
#ifdef IMPROVE_ACCURACY
		// To improve the accuracy of the polygon insidedness test
		// and making the result consist with the distance field
		// gradient, the division forcing 2 things into the same
		// magnitude can be replaced with multiplication, making
		// sure not to flip signs in the process. After rearranging
		// the result is the same as in the algorithm
		// CrossingsMultiplyTest by Eric Haines @
		// http://www.realtimerendering.com/resources/GraphicsGems//gemsiv/ptpoly_haines/ptinpoly.c
		pInPoly = pInPoly!= (a.y> p.y!= b.y> p.y&& a.y> b.y!= (a.x- b.x)* (p.y- b.y)< (a.y- b.y)* (p.x- b.x));
#else
		pInPoly = pInPoly!= (a.y> p.y!= b.y> p.y&& p.x< ((a.x- b.x)* (p.y- b.y))/ (a.y- b.y)+ b.x);
#endif
	pInPolyDist = fOpUnion(pInPolyDist,fLineSegment(p,a,b));
}
float pnpolyEnd() {
#ifdef REPLACE_ZERO_DISTANCE
	if(pInPolyDist== 0.) {
		// Arbitrary, chosen visually to be smaller than values nearby.
		pInPolyDist = 0.00000001490116119384765625;
    }
#endif
	float result = pInPoly? -pInPolyDist: pInPolyDist;
	pInPoly = false;
	pInPolyDist = INFINITY;
	return result;
}
void C(float x1, float y1, float x2, float y2, float x, float y) {
	bezier(vec2(_x,_y),vec2(x1,y1),vec2(x2,y2),vec2(x,y));
	_x = x;
	_y = y;
}
void M(float x, float y) {
	x0 = _x = x;
	y0 = _y = y;
}
void L(float x, float y) {
	vec2 a = vec2(_x,_y);
	vec2 b = vec2(x,y);
	pnpolyAdd(uv,a,b);
	_x = x;
	_y = y;
}
void z() {
	if(pInPolyDist!= INFINITY) {
		if(_x!= x0|| _y!= y0) {
			vec2 _xy = vec2(_x,_y);
			vec2 xy0 = vec2(x0,y0);
			pnpolyAdd(uv,_xy,xy0);
		}
		_d = fOpUnion(_d,pnpolyEnd());
	}
}
void H(float x) {
	L(x,_y);
}
void V(float y) {
	L(_x,y);
}
void c(float x1, float y1, float x2, float y2, float x, float y) {
	C(_x+ x1,_y+ y1,_x+ x2,_y+ y2,_x+ x,_y+ y);
}
void m(float x, float y) {
	M(_x+ x,_y+ y);
}
void l(float x, float y) {
	L(_x+ x,_y+ y);
}
void h(float x) {
	H(_x+ x);
}
void v(float y) {
	V(_y+ y);
}

void Z() {
	z();
}

void style(float fillRule, uint rgb, float diection, float contrastBoost) {
	fill = fillRule;
	dir = diection;
	COL = vec4((rgb>> uint(16))& uint(0xFF),(rgb>> uint(8))& uint(0xFF),rgb& uint(0xFF),1)/ 255.;
	contrast = contrastBoost;
}
void style(float f, uint c, float d) {
	style(f,c,d,1.);
}
void path() {
	if(pInPolyDist!= INFINITY) {
		if(fill> 0.) {
			z();
		} else {
			_d = fOpUnion(_d,abs(pnpolyEnd()));
		}
	}
}

// === SVG drawing ===============================================================

float fSvgWorldIcon(vec3 p) {
	_d = INFINITY;
	uv = p.xz;
    // scaling
	uv *= 80.;
	uv += vec2(29.-251./ 1024.+240./ 1024./ 2048.,7.-33./ 1024.+475./ 1024./ 2048.);

// SVG by Freepik http://www.flaticon.com/free-icon/world_52349
#if 1
	path();
	style(FILL,0x000060u,DIR);  // atlantic ocean
	M(28.755,6.968);
	l(-0.47,0.149);
	L(25.782,7.34);
	l(-0.707,1.129);
	l(-0.513,-0.163);
	L(22.57,6.51);
	l(-0.289,-0.934);
	L(21.894,4.58);
	l(-1.252,-1.123);
	l(-1.477,-0.289);
	l(-0.034,0.676);
	l(1.447,1.412);
	l(0.708,0.834);
	L(20.49,6.506);
	l(-0.648,-0.191);
	L(18.871,5.91);
	l(0.033,-0.783);
	l(-1.274,-0.524);
	l(-0.423,1.841);
	l(-1.284,0.291);
	l(0.127,1.027);
	l(1.673,0.322);
	l(0.289,-1.641);
	l(1.381,0.204);
	l(0.642,0.376);
	h(1.03);
	l(0.705,1.412);
	l(1.869,1.896);
	l(-0.137,0.737);
	l(-1.507,-0.192);
	l(-2.604,1.315);
	l(-1.875,2.249);
	l(-0.244,0.996);
	h(-0.673);
	l(-1.254,-0.578);
	l(-1.218,0.578);
	l(0.303,1.285);
	l(0.53,-0.611);
	l(0.932,-0.029);
	l(-0.065,1.154);
	l(0.772,0.226);
	l(0.771,0.866);
	l(1.259,-0.354);
	l(1.438,0.227);
	l(1.67,0.449);
	l(0.834,0.098);
	l(1.414,1.605);
	l(2.729,1.605);
	l(-1.765,3.372);
	l(-1.863,0.866);
	l(-0.707,1.927);
	l(-2.696,1.8);
	l(-0.287,1.038);
	c(6.892,-1.66,12.019,-7.851,12.019,-15.253);
	C(31.413,12.474,30.433,9.465,28.755,6.968);
	z();
#endif
#if 1
	path();
	style(FILL,0x006000u,DIR);  // pacific ocean
	M(17.515,23.917);
	l(-1.144,-2.121);
	l(1.05,-2.188);
	l(-1.05,-0.314);
	l(-1.179,-1.184);
	l(-2.612,-0.586);
	l(-0.867,-1.814);
	v(1.077);
	h(-0.382);
	l(-2.251,-3.052);
	v(-2.507);
	L(7.43,8.545);
	L(4.81,9.012);
	H(3.045);
	L(2.157,8.43);
	L(3.29,7.532);
	L(2.16,7.793);
	c(-1.362,2.326,-2.156,5.025,-2.156,7.916);
	c(0.,8.673,7.031,15.707,15.705,15.707);
	c(0.668,0.,1.323,-0.059,1.971,-0.137);
	l(-0.164,-1.903);
	c(0.,0.,0.721,-2.826,0.721,-2.922);
	C(18.236,26.357,17.515,23.917,17.515,23.917);
	z();
#endif
#if 1
	path();
	style(FILL,0x600000u,DIR); // polar sea
	M(5.84,5.065);
	l(2.79,-0.389);
	l(1.286,-0.705);
	l(1.447,0.417);
	l(2.312,-0.128);
	l(0.792,-1.245);
	l(1.155,0.19);
	l(2.805,-0.263);
	L(19.2,2.09);
	l(1.09,-0.728);
	l(1.542,0.232);
	l(0.562,-0.085);
	C(20.363,0.553,18.103,0.,15.708,0.);
	C(10.833,0.,6.474,2.222,3.596,5.711);
	h(0.008);
	L(5.84,5.065);
	z();
	M(16.372,1.562);
	l(1.604,-0.883);
	l(1.03,0.595);
	l(-1.491,1.135);
	l(-1.424,0.143);
	l(-0.641,-0.416);
	L(16.372,1.562);
	z();
	M(11.621,1.691);
	l(0.708,0.295);
	l(0.927,-0.295);
	l(0.505,0.875);
	l(-2.14,0.562);
	l(-1.029,-0.602);
	C(10.591,2.526,11.598,1.878,11.621,1.691);
	z();
#endif
    // unscaling
	return _d/80.;
}
//
// scale-invariant distance meter
//
// be less confused by your distances!
//
// (as seen in the screenshots on http://mercury.sexy/hg_sdf/)
//
// tries to paint a plane in useful colors. outputs HDR colors, so can easily
// be integrated into your existing rendering without looking bad.
//
//

// the actual SDF:
float f(vec3 p) {
    return fSvgWorldIcon(p);
}

// tone mapping stolen from zavie's shader
vec3 Uncharted2ToneMapping(vec3 color) {
	float A = 0.15;
	float B = 0.50;
	float C = 0.10;
	float D = 0.20;
	float E = 0.02;
	float F = 0.30;
	float W = 11.2;
	float exposure = 2.;
	color *= exposure;
	color = ((color* (A* color+ C* B)+ D* E)/ (color* (A* color+ B)+ D* F))- E/ F;
	float white = ((W* (A* W+ C* B)+ D* E)/ (W* (A* W+ B)+ D* F))- E/ F;
	color /= white;
	return color;
}

vec3 fusion(float x) {
// the meter uses the "fusion" gradient, which goes from dark magenta (0) to white (1)
// (often seen in heatmaps in papers etc)
	float t = clamp(x,0.0,1.0);
	return clamp(vec3(sqrt(t),t* t* t,max(sin(PI* 1.75* t),pow(t,12.0))),0.0,1.0);
}
vec3 fusionHDR(float x) {
// HDR version
	float t = clamp(x,0.0,1.0);
	return fusion(sqrt(t))* (0.5+ 2.* t);
}

vec3 distanceMeter(float dist, float rayLength, vec3 rayDir, float camHeight) {
// distance meter function. needs a bit more than just the distance
// to estimate the zoom level that it paints at.
//
// if you have real opengl, you can additionally use derivatives (dFdx, dFdy)
// to detect discontinuities, i had to strip that for webgl
//
// visualizing the magnitude of the gradient is also useful
	float idealGridDistance = 20.0/ rayLength* pow(abs(rayDir.y),0.8);
	float nearestBase = floor(log(idealGridDistance)/ log(10.));
	float relativeDist = abs(dist/ camHeight);

	float largerDistance = pow(10.0,nearestBase+ 1.);
	float smallerDistance = pow(10.0,nearestBase);

	vec3 col = fusionHDR(log(1.+ relativeDist));
	col = max(vec3(0.),col);
	if((dist)< 0.) {
		col = col.grb* 3.;
	}

	float l0 = (pow(0.5+ 0.5* cos(dist* PI* 2.* smallerDistance),10.0));
	float l1 = (pow(0.5+ 0.5* cos(dist* PI* 2.* largerDistance),10.0));

	float x = fract(log(idealGridDistance)/ log(10.));
	l0 = mix(l0,0.,smoothstep(0.5,1.0,x));
	l1 = mix(0.,l1,smoothstep(0.0,0.5,x));

	col.rgb *= 0.1+ 0.9* (1.- l0)* (1.- l1);
	return col;
}
vec4 mainImage_meter(vec2 fragCoord) {
	vec2 reso = iResolution.xy;
	vec2 screenCoord = (((2.0* fragCoord)- reso)/ iResolution.y);
	float camDolly = iMouse.z> 0.? iMouse.y/ iResolution.y: (.5)+ (.5)* sin(iTime* .5);
	float camRotation = iMouse.z> 0.? iMouse.x/ iResolution.x* PI* (2.)+ PI: (.2)* iTime;
	vec3 cam_pos = (vec3(0.0,1.0,1.4)* pow(10.,camDolly* 8.- 8.0));
	vec3 cam_dir = normalize(vec3(0.0,-0.8,-1.0));
	vec3 cam_up = normalize(vec3(0.0,1.0,-0.8));
	cam_up.xz = (pR(cam_up.xz,camRotation));
	cam_dir.xz = (pR(cam_dir.xz,camRotation));
	cam_pos.xz = (pR(cam_pos.xz,camRotation));
	float cam_fov = (45.);
	vec3 u = cross(cam_dir,cam_up);
	vec3 v = cam_up;
	vec3 w = -cam_dir;
	vec3 o = cam_pos;
	vec3 d = vec3(screenCoord,-1.0/ tan(radians(.5*cam_fov)));
	d = normalize((mat3(u,v,w)* d));
	float t = INFINITY;
	if(d.y< 0.) {
		t = (o.y)/ -d.y;
	}
	vec3 p = ((d* t)+ o);
	float dist = f(p);
	vec3 col = distanceMeter(dist,t,d,cam_pos.y);
	col = Uncharted2ToneMapping(col);
	vec4 fragColor = vec4(pow(col,vec3(1./ 2.2)),1);
	return fragColor;
}
void mainImage(out vec4 fragColor, vec2 fragCoord) {
	fragColor = mainImage_meter(fragCoord);
}'''


class StSvgDebugAid(GlslSource):
    name = 'st_svg_debug_aid'
    label = 'SVG debug aid'
    description = 'Showing not one but all corner problem areas in Bézier spline distance fields in [url=https://www.shadertoy.com/view/MlVSWc]SVG viewer[/url] by FabriceNeyret2'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
