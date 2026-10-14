"""Hydrogen   Orbits: Hydrogen orbits. Implicit surface shows surface with probability density = const. Meant to see the shapes, not quantitative. m=0 Shapes are cut open. Use mouse...

Source: https://www.shadertoy.com/view/7l23zK
Author: weasel
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2021 Henrik Dick
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

#define PI 3.14159265359

/*** math heavy part for spherical harmonics ***/

#define SQRT2PI 2.506628274631

// factorial
float fac(int n) {
    float res = 1.0;
    for (int i = n; i > 1; i--)
        res *= float(i);
    return res;
}

// double factorial
float dfac(int n) {
    float res = 1.0;
    for (int i = n; i > 1; i-=2)
        res *= float(i);
    return res;
}

// fac(l-m)/fac(l+m) but more stable
float fac2(int l, int m) {
    int am = abs(m);
    if (am > l)
        return 0.0;
    float res = 1.0;
    for (int i = max(l-am+1,2); i <= l+am; i++)
        res *= float(i);
    if (m < 0)
        return res;
    return 1.0 / res;
}

// complex exponential
vec2 cexp(vec2 c) {
    return exp(c.x)*vec2(cos(c.y), sin(c.y));
}

// complex multiplication
vec2 cmul(vec2 a, vec2 b) {
    return vec2(a.x * b.x - a.y * b.y, a.x * b.y + a.y * b.x);
}

// complex conjugation
vec2 conj(vec2 c) { return vec2(c.x, -c.y); }

// complex/real magnitude squared
float sqr(float x) { return x*x; }
float sqr(vec2 x) { return dot(x,x); }

// associated legendre polynomials
float legendre_poly(float x, int l, int m) {
    if (l < abs(m))
        return 0.0;
    if (l == 0)
        return 1.0;
    float mul = m >= 0 ? 1.0 : float((~m&1)*2-1)*fac2(l,m);
    m = abs(m);
    // recursive calculation of legendre polynomial
    float lp1 = 0.0;
    float lp2 = float((~m&1)*2-1)*dfac(2*m-1)*pow(max(1.0-x*x, 1e-7), float(m)/2.0);
    for (int i = m+1; i <= l; i++) {
        float lp = (x*float(2*i-1)*lp2 - float(i+m-1)*lp1)/float(i-m);
        lp1 = lp2; lp2 = lp;
    }
    return lp2 / mul;
}

// spherical harmonics function
vec2 sphere_harm(float theta, float phi, int l, int m) {
    float abs_value = 1.0/SQRT2PI*sqrt(float(2*l+1)/2.0*fac2(l,m))
                        *legendre_poly(cos(theta), l, m);
    return cexp(vec2(0.0,float(m)*phi))*abs_value;
}

// associated laguerre polynomial L_s^k(x) with k > 0, s >= 0
float laguerre_poly(float x, int s, int k) {
    if (s <= 0)
        return 1.0;
    float lp1 = 1.0;
    float lp2 = 1.0 - x + float(k);
    for (int n = 1; n < s; n++) {
        float lp = ((float(2*n + k + 1) - x) * lp2 - float(n+k)*lp1)/float(n+1);
        lp1 = lp2; lp2 = lp;
    }
    return lp2;
}

// radius dependent term of the 1/r potential eigenstates in atomic units
float radius_term(float r, int n, int l) {
    float a0 = 1.0; // atomic radius
    float rr = r / a0;
    float n2 = 2.0 / float(n) / a0;
    float n3 = n2 * n2 * n2;
    float p1 = sqrt(n3 * fac2(n, l) * float(n-l)/float(n));
    float p2 = exp(-rr/float(n));
    float p3 = pow(n2*r, float(l));
    float p4 = laguerre_poly(n2*r, n-l-1, 2*l+1);
    return p1 * p2 * p3 * p4;
}

vec2 hydrogen(vec3 pos, int n, int l, int m) {
    float r = length(pos);
    float sin_theta = length(pos.xy);
    float phi = sin_theta > 0.0 ? atan(pos.x, pos.y) : 0.0;
    float theta = atan(sin_theta, pos.z);//atan(sin_theta, pos.z);
    
    return sphere_harm(theta, phi, l, m) * radius_term(r, n, l);
}

/*** Now the rendering ***/

vec3 rotateX(vec3 pos, float angle) {
    return vec3(pos.x, cmul(pos.yz, cexp(vec2(0.,-angle))));
}

#define SELECT_GRID 7.0
void get_nlm(out int n, out int l, out int m, in vec2 fragCoord) {
    vec2 mouse = iMouse.xy/iResolution.xy;
    int t;
    
    bool selection = false;
    if (mouse.x + mouse.y > 0.0) {// && iMouse.z > 0.5) {
        vec2 coord = iMouse.z > 0.5 ? fragCoord : iMouse.xy;
        ivec2 cell = ivec2(coord/iResolution.y*SELECT_GRID);
        //t = (int(SELECT_GRID) - cell.y - 1) + cell.x * int(SELECT_GRID);
        t = cell.x;
        n = cell.y + 1;
        selection = t < n*(n+1)/2 || iMouse.z > 0.5;
    }
    if (!selection) {
        /*vec2 coord = fragCoord;
        ivec2 cell = ivec2(coord/iResolution.y*SELECT_GRID);
        cell.x += 0;
        t = (int(SELECT_GRID) - cell.y - 1) + cell.x * int(SELECT_GRID);*/
        t = int(iTime*0.5);
        
        if (t == 0)
            n = 1;
        else {
            float x = float(t);
            // see https://en.wikipedia.org/wiki/Tetrahedral_number
            n = int(ceil(pow(3.*x+sqrt(9.*x*x-1./27.), 1./3.) + pow(3.*x-sqrt(9.*x*x-1./27.), 1./3.) - 0.995));
        }
        t -= ((n*(n-1)*(2*n-1))/6+(n*(n-1))/2)/2;
    }
    
    l = int(floor(sqrt(0.25 + float(2*t)) - 0.5));
    m = t - int(floor(0.5*float(l + l*l)));
}

float spos(float x, float s) {
    return 0.5*(x*x/(s+abs(x))+x+s);
}
float smax(float a, float b, float s) {
    return a+spos(b-a,s);
}

#define SURFACE_LEVEL 0.3
float globalSdf(vec3 pos, out vec3 color, in vec2 fragCoord) {
    int n, m, l;
    get_nlm(n, l, m, fragCoord);

    // evaluate spherical harmonics
    vec2 off = cexp(vec2(0, iTime));
    
    vec2 H = hydrogen(pos*float(n*n+1)*1.5, n, l, m);
    if (m != 0) H = cmul(H, off);
    
    H *= float((l+1)*l+n*n)*sqrt(float(n)); // visual rescaling
    
    float crit2 = 0.3*(length(pos)+0.05);
    
    color = H.x > 0. ? vec3(1.0,0.6,0.15) : vec3(0.2,0.4,0.5);
    //color = vec3(max(vec3(0.02),(sin(float(n) + vec3(0., 2.1, 4.2)))));
    float d = (SURFACE_LEVEL - abs(H.x))*crit2;
    if (m == 0)
        return smax(d, 0.707*(pos.x+pos.y), 0.02);
    return d;
    
    float arg = atan(H.x, H.y);
    color = vec3(max(vec3(0.02),(sin(arg + vec3(0., 2.1, 4.2)))));
    return (0.20 - length(H))*crit2;
}

vec3 calculate_normal(in vec3 world_point, float sd, in vec2 fragCoord) {
    const vec3 small_step = vec3(0.001, 0.0, 0.0);
    vec3 col;
    float gradient_x = globalSdf(world_point + small_step.xyy, col, fragCoord) - sd;
    float gradient_y = globalSdf(world_point + small_step.yxy, col, fragCoord) - sd;
    float gradient_z = globalSdf(world_point + small_step.yyx, col, fragCoord) - sd;
    vec3 normal = vec3(gradient_x, gradient_y, gradient_z);
    return normalize(normal);
}

vec4 lighting(vec3 cp, vec3 color, vec3 normal, vec3 rdir) {
    // from https://www.shadertoy.com/view/ts3XDj
    // geometry
    vec3 ref = reflect( rdir, normal );

    // material		
    vec3 mate = color.rgb;

    float occ = clamp(length(cp)*0.7, 0.2, 0.5);//min(color.g, 1.0);//clamp(2.0*tmat.z, 0.0, 1.0);
    float sss = -pow(clamp(1.0 + dot(normal, rdir), 0.0, 1.0), 1.0);

    // lights
    vec3 lin  = 2.5*occ*vec3(1.0)*(0.6 + 0.4*normal.y);
         lin += 1.0*sss*vec3(1.0,0.95,0.70)*occ;	

    // surface-light interacion
    vec3 col = mate.xyz * lin;
    return vec4(col, 1.0);
}

#define NUMBER_OF_STEPS 128
#define MINIMUM_HIT_DISTANCE 0.005
#define MAXIMUM_TRACE_DISTANCE 6.0
vec4 raymarch(in vec3 rpos, in vec3 rdir, in vec2 fragCoord) {
    float t = 0.0;
    float closest_t = 0.0;
    float closest_t_r = MAXIMUM_TRACE_DISTANCE;
    float closest_t_r2 = MAXIMUM_TRACE_DISTANCE;
    float closest_t_r3 = MAXIMUM_TRACE_DISTANCE;
    vec4 col = vec4(0,0,0,0);
    for (int i = 0; i < NUMBER_OF_STEPS; i++) {
        vec3 cp = rpos + t * rdir;
        
        vec3 color = vec3(0.0);
		float sd = globalSdf(cp, color, fragCoord);
        
        if (abs(sd) < 0.7*MINIMUM_HIT_DISTANCE) {
            vec3 normal = calculate_normal(cp, sd, fragCoord);
            col = lighting(cp, color, normal, rdir);
            break;
        }
        
        closest_t_r3 = closest_t_r2;
        closest_t_r2 = closest_t_r;
        if (sd < closest_t_r) {
            closest_t = t;
            closest_t_r = sd;
        }

        if (t > MAXIMUM_TRACE_DISTANCE)
            break;
        
        t += sd;
    }
    if (abs(closest_t_r3) > MINIMUM_HIT_DISTANCE) {
        return col;
    }
    vec3 cp = rpos + closest_t * rdir;
    vec3 color = vec3(0.0);
    float sd = globalSdf(cp, color, fragCoord);
    vec3 normal = calculate_normal(cp, sd, fragCoord);
    float a = 1.0-abs(closest_t_r3)/MINIMUM_HIT_DISTANCE;
    vec4 col2 = lighting(cp, color, normal, rdir);
    col2.a = a;
    return mix(col, col2, a);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = (2.0*fragCoord - iResolution.xy) / iResolution.y;
    float rot = 0.5*sin(iTime*0.5) * PI/3.0;
    
    if (iMouse.z > 0.5) {
        // selection on click
        uv = fract(fragCoord/iResolution.y*SELECT_GRID)*2.0-1.0;
        rot = -0.5;
    }

     // camera movement
	vec3 cam_pos = 3.0 * rotateX(vec3(0,1,0), rot);
    vec3 look_at = vec3(0);   
    vec3 look_up = vec3(0,0,1);
    // camera matrix
    vec3 ww = normalize(look_at - cam_pos);
    vec3 uu = normalize(cross(ww, look_up));
    vec3 vv = normalize(cross(uu, ww));
	// create perspective view ray
    vec3 rpos = cam_pos;
	vec3 rdir = normalize( uv.x*uu + uv.y*vv + 2.0*ww );
    
    vec4 col = raymarch(rpos, rdir, fragCoord);
    vec3 bg = vec3(0.3) * clamp(1.0-2.6*length(fragCoord/iResolution.xy-0.5)*0.5,0.0,1.0);
    col = vec4(mix(bg, col.rgb, col.a), 1.0);
    col = vec4(pow(clamp(col.rgb,0.0,1.0), vec3(0.4545)), 1.0);

    if (iMouse.z > 0.5) {
        // selection on click
        ivec2 select = abs(ivec2(fragCoord/iResolution.y*SELECT_GRID)-ivec2(iMouse.xy/iResolution.y*SELECT_GRID));
        if (select.x + select.y == 0) {
            // draw selection box
            vec2 absuv = abs(uv);
            vec2 cmp = min(absuv, vec2(0.9));
            float d = length(absuv - cmp);
            float fac = max(0.05 - abs(d - 0.05), 0.0)/0.025;
            ivec2 checkers = ivec2(round(uv * 3.5));
            fac *= float((checkers.x + checkers.y + 1)&1);
            col = mix(col, vec4(1.0), fac);
        }
    }
    
    fragColor = col;
}'''


class StHydrogenOrbits(GlslSource):
    name = 'st_hydrogen_orbits'
    label = 'Hydrogen   Orbits'
    description = 'Hydrogen orbits. Implicit surface shows surface with probability density = const. Meant to see the shapes, not quantitative. m=0 Shapes are cut open. Use mouse...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.4)
