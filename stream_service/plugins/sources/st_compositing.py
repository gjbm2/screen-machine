"""Compositing: Compares compositing layers back to front and front to back. This can be done easily by using premultiplied alpha. Also, blending in linear space.

Source: https://www.shadertoy.com/view/Xlsyzf
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2017 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Compares compositing layers back to front and front to back.
// Inspired by ollj's https://www.shadertoy.com/view/4tscRf


// enable to try back_to_front compositing
//#define BACK_TO_FRONT



const float kGamma = 2.2;   // set this to 1.0 to see the wrong way to blend colors (the way Photoshop does)

vec4 over( in vec4 a, in vec4 b )
{
    return a + b*(1.0-a.w);
}

vec4 gamma2linear_premultalpha( vec4 c )
{
    return vec4( pow( c.xyz, vec3(kGamma) )*c.w, c.w);
}

vec4 linear2gamma_premultalpha( vec4 c )
{
    return vec4( pow(c.xyz/c.w,vec3(1.0/kGamma) ), 1.0 );
}
    
void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 u = (-iResolution.xy+2.0*fragCoord)/min(iResolution.x,iResolution.y);

    // patterns
    float patChecker = mod(floor(3.0*u.x)+floor(3.0*u.y),2.0);
    float patCircle1 = 1.0-smoothstep(-0.2,0.2,length(u-.4*sin(0.43*iTime+vec2(0.0,1.0)))-0.7);
    float patCircle2 = 1.0-smoothstep(-0.2,0.2,length(u-.4*sin(0.41*iTime+vec2(3.0,2.0)))-0.7);
    float patCircle3 = 1.0-smoothstep(-0.2,0.2,length(u-.4*sin(0.53*iTime+vec2(4.0,1.0)))-0.7);
    
    // colors. Note gamma and premultiplication
    vec4 c0 = gamma2linear_premultalpha(vec4(vec3(patChecker*.2+.7),1.0));
    vec4 c1 = gamma2linear_premultalpha(vec4(.9,.1,.1,patCircle1));
    vec4 c2 = gamma2linear_premultalpha(vec4(.1,.9,.1,patCircle2));
    vec4 c3 = gamma2linear_premultalpha(vec4(.1,.1,.9,patCircle3));

    vec4 cr;
    
    // Compositing of layers!
    //  First, back to front
    //  Second, front to back
    // These two versions below should result in the same image
    
    #ifdef BACK_TO_FRONT
     cr = c0;           // checkers background
     cr = over(c1,cr);  // cover with red circle
     cr = over(c2,cr);  // cover with green circle
     cr = over(c3,cr);  // cover with blue circle
    #else    
     cr = c3;           // blue circle
     cr = over(cr,c2);  // over green circle
     cr = over(cr,c1);  // over red circle
     cr = over(cr,c0);  // over checkers background
    #endif 

    // undo premultiply and gamma, for display
    fragColor = linear2gamma_premultalpha( cr );
}'''


class StCompositing(GlslSource):
    name = 'st_compositing'
    label = 'Compositing'
    description = 'Compares compositing layers back to front and front to back. This can be done easily by using premultiplied alpha. Also, blending in linear space.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
