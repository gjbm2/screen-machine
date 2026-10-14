"""Patiently: used iq's cosine filtering as a base

Source: https://www.shadertoy.com/view/flXXR4
Author: vivavolt
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// A simple way to prevent aliasing of cosine functions (the color
// palette in this case is made of 8 layers) by attenuating them
// when their oscillations become smaller than a pixel. Left is
// direct use of cos(x), right is band-limited cos(x).
//
// Box-filtering of cos(x):
//
// (1/w)∫cos(t)dt with t ∈ (x-½w, x+½w)
// = [sin(x+½w) - sin(x-½w)]/w
// = cos(x)·sin(½w)/(½w)
//
// Can approximate smoothstep(2π,0,w) ≈ sin(w/2)/(w/2),
// which you can also see as attenuating cos(x) when it 
// oscilates more than once per pixel. More info:
//
// https://iquilezles.org/www/articles/bandlimiting/bandlimiting.htm
//
// Related Shader:
//   https://www.shadertoy.com/view/WtScDt
//   https://www.shadertoy.com/view/wtXfRH
//   https://www.shadertoy.com/view/3tScWd

bool mode = true;

vec3 fcos( vec3 x )
{
    if( mode) return cos(x);                // naive

    vec3 w = fwidth(x);
    #if 0
    return cos(x) * sin(0.5*w)/(0.5*w);     // filtered-exact
	#else
    return cos(x) * smoothstep(6.28,0.0,w); // filtered-approx
	#endif  
}

vec3 getColorB( in float t, float k )
{
    vec3 col = vec3(0.4,0.4,0.4);
    col += 0.12*fcos(6.28318*t*  1.0+vec3(0.0,0.2+k,1.1));
    col += 0.11*fcos(6.28318*t*  3.1+vec3(0.3,1.4,0.1+k));
    col += 0.10*fcos(6.28318*t*  5.1+vec3(0.1+k,0.7,1.1));
    col += 0.09*fcos(6.28318*t*  9.1+vec3(0.2,0.8+k,1.4));
    return col;
}

vec3 getColor( in float t )
{
    vec3 col = vec3(0.4,0.4,0.4);
    col += 0.12*fcos(6.28318*t*  1.0+vec3(0.0,0.8,1.1));
    col += 0.11*fcos(6.28318*t*  3.1+vec3(0.3,0.4,0.1));
    col += 0.10*fcos(6.28318*t*  5.1+vec3(0.1,0.7,1.1));
    col += 0.09*fcos(6.28318*t*  9.1+vec3(0.2,0.8,1.4));
    col += 0.08*fcos(6.28318*t* 17.1+vec3(0.2,0.6,0.7));
    col += 0.07*fcos(6.28318*t* 31.1+vec3(0.1,0.6,0.7));
    col += 0.06*fcos(6.28318*t* 65.1+vec3(0.0,0.5,0.8));
    col += 0.06*fcos(6.28318*t*115.1+vec3(0.1,0.4,0.7));
    col += 0.09*fcos(6.28318*t*265.1+vec3(1.1,1.4,2.7));
    return col;
}

vec2 deform( in vec2 p, float t, float k )
{
    // deform 1
    p *= 0.55;
    p = mix(p, 3.*p/dot(p,p), .1);
    p.x += t*0.1;
    
    // deform 2
    p += k*0.2*cos( 5.*k+ 1.5*p.yx + 0.03*1.0*t + vec2(0.1,1.1) );
    p += k*0.2*sin( 5.*k+ 2.4*p.yx + 0.03*1.6*t + vec2(4.5,2.6) );
    p += k*0.2*cos( 5.*k+ 3.3*p.yx + 0.03*1.2*t + vec2(3.2,3.4) );
    p += k*0.2*sin( 5.*k+ 4.2*p.yx + 0.03*1.7*t + vec2(1.8,5.2) );
    p += k*0.2*cos( 5.*k+ 9.1*p.yx + 0.03*1.1*t + vec2(6.3,3.9) );
    
    return p;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord )
{
    // pixellate
    float d = iResolution.x / 320.;
    fragCoord = floor(fragCoord / d)*d;
    
    // coordiantes
	vec2 p = (2.0*fragCoord-iResolution.xy)/iResolution.y;
    

    vec2 uv = fragCoord / iResolution.xy;
    vec2 w = p;
    
    
    // the sound texture is 512x2
    int tx = int(uv.x*512.0);
    int ty = int(uv.y*512.0);
    float sx = texelFetch(iChannel0, ivec2(tx, 0), 0).x;
    float sy = texelFetch(iChannel0, ivec2(ty, 0), 0).x;
    float ss = .5*sin(sx) + .5*cos(sy);
    //ss = 1.;

    // deformation
    p = deform( p, iTime * 20. + (ss * 30.) , ss * 1.);

    // base color pattern
    vec3 col = getColorB( 0.5*length(p ), ss*10.);
    
 
    // output
    fragColor = vec4( col, 1.0 );
}'''


class StPatiently(GlslSource):
    name = 'st_patiently'
    label = 'Patiently'
    description = "used iq's cosine filtering as a base"
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.5)
