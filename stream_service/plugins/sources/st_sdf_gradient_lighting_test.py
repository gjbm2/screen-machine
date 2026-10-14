"""SDF Gradient Lighting Test: using the gradient to get some sort of lighting effect

Source: https://www.shadertoy.com/view/7ls3Wf
Author: ENDESGA
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Signed distance and gradient of the smooth minimum
// operator. This is for a quadratic polynomial smooth
// minium function (https://iquilezles.org/www/articles/smin/smin.htm)
//
// Please note that the smooth-minimum is not a SDF
// preserving operator. It's a good approximation when far
// enough from the surfaces, but quickly distorts near them.
//
// Because of that, the gradient output from it, although 
// correct, has no longer length of 1.0
//
// List of other 2D distances+gradients:
//
// https://iquilezles.org/www/articles/distgradfunctions2d/distgradfunctions2d.htm
//
// and
//
// https://www.shadertoy.com/playlist/M3dSRf


// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .yz = ∇f(p)
vec3 sdgSMin( in vec3 a, in vec3 b, in float k )
{
    float h = max(k-abs(a.x-b.x),0.0);
    float m = 0.25*h*h/k;
    float n = 0.50*  h/k;
    return vec3( min(a.x,  b.x) - m, 
                 mix(a.yz, b.yz, (a.x<b.x)?n:1.0-n) );
}

// .x = f(p)
// .y = ∂f(p)/∂x
// .z = ∂f(p)/∂y
// .yz = ∇f(p) with ‖∇f(p)‖ = 1
vec3 sdgMin( in vec3 a, in vec3 b )
{
    return (a.x<b.x) ? a : b;
}

vec3 sdgCircle( in vec2 p, in float r ) 
{
    float d = length(p);
    return vec3( d-r, p/d );
}

vec3 sdgBox( in vec2 p, in vec2 b )
{
    vec2 w = abs(p)-b;
    vec2 s = vec2(p.x<0.0?-1:1,p.y<0.0?-1:1);
    
    float g = max(w.x,w.y);
	vec2  q = max(w,0.0);
    float l = length(q);
    
    return vec3(   (g>0.0)?l   : g,
                s*((g>0.0)?q/l : ((w.x>w.y)?vec2(1,0):vec2(0,1))));
}

vec3 sdgSegment( in vec2 p, in vec2 a, in vec2 b )
{
    vec2 ba = b-a;
    vec2 pa = p-a;
    float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
    vec2  q = pa-h*ba;
    float d = length(q);
    return vec3(d,q/d);
}

vec3 scene(in vec2 p) {
    vec3 dg1 = sdgBox(p,vec2(0.8,0.3));
    vec3 dg2 = sdgSegment( p, vec2(sin(iTime),-0.5), vec2(cos(iTime*1.777),0.7) ) - vec3(0.15,0.0,0.0);
    return sdgSMin(dg1,dg2,0.1);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    p *= 1.5;

    // sdf(p) and gradient(sdf(p))
    

  //vec3 dg = sdgMin(dg1,dg2);
    vec3 dg = scene(p);//sdgSMin(dg1,dg2,0.1);
    float d = dg.x;
    vec2  g = dg.yz;
    vec2 sun_pos = p - vec2(sin(iTime) * 2.5,cos(iTime) * 1.0);
    vec3 sun = sdgCircle(sun_pos,0.01);//vec2(sin(iTime),cos(iTime));
    
    //if (abs(sun.x)-0.01 <= 0.0) {
        //fragColor = vec4(0.25,1.0,1.0,1.0);
    //}
    //else
    {
    
    // central differenes based gradient, for comparison
    // g = vec2(dFdx(d),dFdy(d))/(2.0/iResolution.y);

	// coloring
    /*vec3 col = (d>0.0) ? vec3(0.9,0.6,0.3) : vec3(0.4,0.7,0.85);
    col *= 1.0 + vec3(0.5*g,0.0);
  //col = vec3(0.5+0.5*g,1.0);
    col *= 1.0 - 0.5*exp(-16.0*abs(d));
	col *= 0.9 + 0.1*cos(150.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(d)) );*/
    
    vec3 col = vec3(1.0,0.0,0.25);
    
    /*if (d <= 0.0 && d >= -0.03) {
        //if (dot(g,sun.yz) <= 0.0)
        col = (vec3(1.0,0.0,0.25) / (sun.x)) * dot(-g,sun.yz);
    } else
    if (d > 0.0) {// && dot(g,sun.yz) <= 0.0) {
        //float td = (clamp((0.92 / (sun.x * (4.0 - dot(g,sun.yz)))),0.0,1.0));// / (sun.x*32.0);
        float td = sqrt(1.0 - (1.0 / (sun.x * sun.x * -4.0))) / 4.0;
        td *= clamp(td,0.0,1.0);
        col = vec3(td,0,td*0.25);
    }*/
    
    //if () col *= 0.0;//((vec3(1.0,0.0,0.25) / (sun.x)) * dot(-g,sun.yz));// * smoothstep(0.0,sun.x,(scene(sun_pos)).x);
    
    col *= scene(sun_pos).x;
    if (d >= 0.0 || d <= -0.03) col *= 0.333;
    
	fragColor = vec4(col,1.0);
    }
}'''


class StSdfGradientLightingTest(GlslSource):
    name = 'st_sdf_gradient_lighting_test'
    label = 'SDF Gradient Lighting Test'
    description = 'using the gradient to get some sort of lighting effect'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
