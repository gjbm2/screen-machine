"""Islandshapes3d: same as the 2d version...

Source: https://www.shadertoy.com/view/7lV3W3
Author: Del
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// island 3d (extrude) - added the colouring from the 2d version + 3 islands
//
// This is basically a fork of the Original SDF Quadratic Bézier Shape here: https://www.shadertoy.com/view/ftdGDB
// slightly modified to produce random Island shapes from a vec2 seed value.
// Golf anyone? :)

// Author: Thomas Stehle
// Title: SDF Quadratic Bézier Shape
//
// The MIT License
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
//
// Signed distance function for a shape made out of quadratic
// Bézier curves in the tradition of iq's series such as this
// one: https://www.shadertoy.com/view/MlKcDD.
//
// The only real contribution of this shader is that it postpones
// the call to the costly Bézier SDF up until the point it has
// identified the closest segment of the control polygon.
// So instead of iterating over all Bézier curves, we iterate
// over the line segments of the control polygon and identify the
// closest segment. We then call the Bézier SDF for this segment
// only. This approach is correct since quadratic Bézier curves
// are always contained in the triangle formed by its three
// control points.
// Constants
const int CAPACITY = 8; // Control polygon capacity
const float INF   = 1.0 / 0.0;
const float SQRT3 = 1.732050807568877;

// Cross-product of two 2D vectors
float cross2(in vec2 a, in vec2 b) {
    return a.x*b.y - a.y*b.x;
}

// Clamp a value to [0, 1]
float saturate(in float a) {
    return clamp(a, 0.0, 1.0);
}
vec3 saturate(in vec3 a) {
    return clamp(a, 0.0, 1.0);
}

// Minimum of the absolute of two values
float abs_min(float a, float b) {
    return abs(a) < abs(b) ? a : b;
}

// SDF for a line segment
float sdf_line(in vec2 p, in vec2 a, in vec2 b) {
    float h = saturate(dot(p - a, b - a) /
                       dot(b - a, b - a));
    return length(p - a - h * (b - a));
}

// Like the SDF for a line but partitioning space into positive and negative
float sdf_line_partition(in vec2 p, in vec2 a, in vec2 b) {
    vec2 ba = b - a;
    vec2 pa = p - a;
    float h = saturate(dot(pa, ba) / dot(ba, ba));
    vec2 k = pa - h * ba;
    vec2 n = vec2(ba.y, -ba.x);
    return (dot(k,n) >= 0.0) ? length(k) : -length(k);
}

// Signed distance to a quadratic Bézier curve
// Mostly identical to https://www.shadertoy.com/view/MlKcDD
// with some additions to combat degenerate cases.
float sdf_bezier(in vec2 pos, in vec2 A, in vec2 B, in vec2 C) {
    const float EPSILON = 1e-3;
    const float ONE_THIRD = 1.0 / 3.0;

    // Handle cases where points coincide
    bool abEqual = all(equal(A, B));
    bool bcEqual = all(equal(B, C));
    bool acEqual = all(equal(A, C));
    
    if (abEqual && bcEqual) {
        return distance(pos, A);
    } else if (abEqual || acEqual) {
        return sdf_line_partition(pos, B, C);
    } else if (bcEqual) {
        return sdf_line_partition(pos, A, C);
    }
    
    // Handle colinear points
    if (abs(dot(normalize(B - A), normalize(C - B)) - 1.0) < EPSILON) {
        return sdf_line_partition(pos, A, C);
    }
    
    vec2 a = B - A;
    vec2 b = A - 2.0*B + C;
    vec2 c = a * 2.0;
    vec2 d = A - pos;

    float kk = 1.0 / dot(b,b);
    float kx = kk * dot(a,b);
    float ky = kk * (2.0*dot(a,a)+dot(d,b)) * ONE_THIRD;
    float kz = kk * dot(d,a);

    float res = 0.0;
    float sgn = 0.0;

    float p = ky - kx*kx;
    float p3 = p*p*p;
    float q = kx*(2.0*kx*kx - 3.0*ky) + kz;
    float h = q*q + 4.0*p3;

    if (h >= 0.0) {
        // One root
        h = sqrt(h);
        vec2 x = 0.5 * (vec2(h, -h) - q);
        vec2 uv = sign(x) * pow(abs(x), vec2(ONE_THIRD));
        float t = saturate(uv.x + uv.y - kx) + EPSILON;
        vec2 q = d + (c + b*t) * t;
        res = dot(q, q);
        sgn = cross2(c + 2.0*b*t, q);
    } else {
        // Three roots
        float z = sqrt(-p);
        float v = acos(q/(p*z*2.0)) * ONE_THIRD;
        float m = cos(v);
        float n = sin(v) * SQRT3;
        vec3 t = saturate(vec3(m+m,-n-m,n-m)*z-kx) + EPSILON;
        vec2 qx = d + (c+b*t.x)*t.x;
        float dx = dot(qx, qx);
        float sx = cross2(c+2.0*b*t.x, qx);
        vec2 qy = d + (c+b*t.y)*t.y;
        float dy = dot(qy, qy);
        float sy = cross2(c+2.0*b*t.y, qy);
        res = (dx < dy) ? dx : dy;
        sgn = (dx < dy) ? sx : sy;
    }
    
    return sign(sgn) * sqrt(res);
}

// Signed distance to a segment of a control polygon
float sdf_control_segment(in vec2 p, in vec2 A, in vec2 B, in vec2 C) {
    return abs_min(sdf_line(p, A, B), sdf_line(p, B, C));
}


// Signed distance to a quadratic Bezier shape made from a given control polygon
float sdf_bezier_shape(in vec2 p, in vec2 controlPoly[CAPACITY])
{
    // Determine closest segment in control polygon
    vec2 closest[3];
    
    // Signed distance to a control polygon
    // Identifies and returns distance to the closest segment.
    // Cycle through segments and track the closest
    float d = INF;
    float ds = 0.0;

    // First n-2 segments
    vec2 c = 0.5 * (controlPoly[0] + controlPoly[1]);
    vec2 prev = c;
    for (int i = 1; i < CAPACITY - 1; ++i) {
        prev = c;
        c = 0.5 * (controlPoly[i] + controlPoly[i+1]);
        ds = sdf_control_segment(p, prev, controlPoly[i], c);
        if (abs(ds) < abs(d)) {
            closest[0] = prev;
            closest[1] = controlPoly[i];
            closest[2] = c;
            d = ds;
        }
    }

    // Last-but-one segment
    prev = c;
    c = 0.5 * (controlPoly[CAPACITY-1] + controlPoly[0]);
    ds = sdf_control_segment(p, prev, controlPoly[CAPACITY-1], c);
    if (abs(ds) < abs(d)) {
        closest[0] = prev;
        closest[1] = controlPoly[CAPACITY-1];
        closest[2] = c;
        d = ds;
    }

    // Last segment
    prev = c;
    c = 0.5 * (controlPoly[0] + controlPoly[1]);
    ds = sdf_control_segment(p, prev, controlPoly[0], c);
    if (abs(ds) < abs(d)) {
        closest[0] = prev;
        closest[1] = controlPoly[0];
        closest[2] = c;
        d = ds;
    }
    // d = distance to outer control polygon
    // Refine by determining actual distance to curve of closest segment
    return sdf_bezier(p, closest[0], closest[1], closest[2]);
}

vec2 hash2( vec2 p )
{
    // procedural white noise	
	return fract(sin(vec2(dot(p,vec2(127.1,311.7)),dot(p,vec2(269.5,183.3))))*43758.5453);
}

float remap(float s, float a1, float a2, float b1, float b2)
{
    return b1 + (s-a1)*(b2-b1)/(a2-a1);
}

// min/max polynomial
float smin( float a, float b, float k )
{
	float h = clamp( 0.5 + 0.5*(b-a)/k, 0.0, 1.0 );
	return mix( b, a, h ) - k*h*(1.0-h);
}

float sdIsland(vec2 p,vec2 seed)
{
    // Control polygon shape
    vec2 controlPoints[CAPACITY];
    
    float tt1 = seed.x;
    float tt2 = seed.y;
    for (int i=0;i<8;i++)
    {
        float a = ((-6.28)/8.0) * float(i);
        float x = sin(a)*1.4;
        float y = cos(a);
        vec2 pos = vec2(x,y);
        vec2 hash = hash2(pos+vec2(tt1*0.15,tt1*0.35));
        hash = 0.5+sin(tt2+sin(hash*151.45))*0.5;
        float rad = 0.65+(hash.x*0.6);
        rad -= hash.y*0.55;
        controlPoints[i] = vec2(x,y)*rad;
    }
    // Distance to shape
    return sdf_bezier_shape(p, controlPoints);
}

float opExtrusion( in vec3 p, float dd, in float h )
{
    float d = dd;
    vec2 w = vec2( d, abs(p.y) - h );
    return min(max(w.x,w.y),0.0) + length(max(w,0.0));
}

vec3 GetWater(vec2 p)
{
    const vec3 col = vec3(0.02,.3,.55);
    const mat3 _m = mat3(-2.0,-1.0,2.0, 3.0,-2.0,1.0, 1.0,2.0,2.0);
    vec4 d = vec4(iDate*.22);
    d.xy = p;
    d.xyw *=_m*.5;
    float v1 = length(.5-fract(d.xyw));
    d.xyw *=_m*.4;
    float v2 = length(.5-fract(d.xyw));
    d.xyw *=_m*.3;
    float v3 = length(.5-fract(d.xyw));
    float v = pow(min(min(v1,v2),v3), 7.)*15.;
    return col+vec3(v,v,v);
}

float basemat;

vec3 GetCol(float mat,vec3 pos)
{
    if (mat<=1.0)
    {
        float sandline = abs(sin(fract(22.0*(pos.z+sin(pos.x*6.0)*0.02))));
        sandline = (1.0-step(sandline,0.5))*0.05;
        vec3 sandcol =  vec3(0.9,0.85,0.2);  
        return sandcol+=sandcol*sandline;    
    }
    else if (mat<=2.0)
    {
        float grassline = 0.5+sin(fract(5.0*(pos.x-pos.z*1.2))*6.28)*0.5;
        grassline = smoothstep(0.35,0.65,grassline)*0.4;
        vec3 grasscol = vec3(0.05,0.6,0.05);
        return grasscol += grasscol*grassline;
    }
    return GetWater(pos.xz);
}

float map( in vec3 pos )
{
    vec2 seed = vec2(floor(iTime*0.25),fract(iTime*0.2)*6.28);
    float dd1 = sdIsland(pos.xz,seed);
    
    float height = 0.05;
    float d1 = opExtrusion(pos,dd1,height)-0.04;

    height = 0.025;
    float d2 = opExtrusion(pos+vec3(0.0,0.1,0.0),dd1-0.2,height)-0.02;
    
    basemat = 0.5;
    if (d2>d1)
      basemat = 1.5;
    d1 = smin(d1,d2,0.05);
    
    float wd = pos.y+0.15;
    
    if (wd<d1)
        basemat=2.5;
    
    d1 = smin(d1,wd,0.25);
    
    return d1;
}


    
// http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
#define ZERO (min(iFrame,0))

vec3 calcNormal( in vec3 pos )
{
#if 0
    vec2 e = vec2(1.0,-1.0)*0.5773*0.0005;
    return normalize( e.xyy*map( pos + e.xyy ).x + 
					  e.yyx*map( pos + e.yyx ).x + 
					  e.yxy*map( pos + e.yxy ).x + 
					  e.xxx*map( pos + e.xxx ).x );
#else
    // inspired by tdhooper and klems - a way to prevent the compiler from inlining map() 4 times
    vec3 n = vec3(0.0);
    for( int i=ZERO; i<4; i++ )
    {
        vec3 e = 0.5773*(2.0*vec3((((i+3)>>1)&1),((i>>1)&1),(i&1))-1.0);
//        n += e*map(pos+0.0005*e);
        n += e*map(pos+0.05*e);
      //if( n.x+n.y+n.z>100.0 ) break;
    }
    return normalize(n);
#endif    
}
    
    
#define AA 0

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // camera movement	
        float an =0.0;//0.25*iTime;
    float hh = mix(0.75,1.5, 0.5+sin(iTime)*0.5);
    
	vec3 ro = vec3( 3.*cos(an), hh, 3.*sin(an) );
    vec3 ta = vec3( 0.0, 0.0, 0.0 );
    // camera matrix
    vec3 ww = normalize( ta - ro );
    vec3 uu = normalize( cross(ww,vec3(0.0,1.0,0.0) ) );
    vec3 vv = normalize( cross(uu,ww));
    
    vec3 tot = vec3(0.0);
    
    #if AA>1
    for( int m=0; m<AA; m++ )
    for( int n=0; n<AA; n++ )
    {
        // pixel coordinates
        vec2 o = vec2(float(m),float(n)) / float(AA) - 0.5;
        vec2 p = (-iResolution.xy + 2.0*(fragCoord+o))/iResolution.y;
        #else    
        vec2 p = (-iResolution.xy + 2.0*fragCoord)/iResolution.y;
        #endif

	    // create view ray
        vec3 rd = normalize( p.x*uu + p.y*vv + 1.8*ww );

        // raymarch
        const float tmax = 20.0;
        float t = 0.0;
        for( int i=0; i<128; i++ )
        {
            vec3 pos = ro + t*rd;
            float h = map(pos);
            if( h<0.0001 || t>tmax ) break;
            t += h;
        }
    
        // shading/lighting
        vec3 col = vec3(0.4,0.5,0.9);
        if( t<tmax )
        {
            vec3 pos = ro + t*rd;
            vec3 nor = calcNormal(pos);
            float dif = clamp( dot(nor,vec3(0.7,0.6,0.4)), 0.0, 1.0 );
            float amb = 0.5 + 0.5*dot(nor,vec3(0.0,0.8,0.6));
            vec3 basecol = GetCol(basemat,pos);
            col = vec3(0.1,0.1,0.1)*amb + basecol*dif;
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


class StIslandshapes3d(GlslSource):
    name = 'st_islandshapes3d'
    label = 'Islandshapes3d'
    description = 'same as the 2d version...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
