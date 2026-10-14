"""NR fractal transitions: Generate the Newton-Raphson fractal for a general cubic polynomial, using Horner's method.

Source: https://www.shadertoy.com/view/WlX3z2
Author: tpfto
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 J. M.
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Newton-Raphson fractal implementation, using Horner's method

#define PI 3.14159265359
#define SCALE 7.5 // plot scale

// from Fabrice Neyret, 
#define cmul(A, B) ( mat2( A, -(A).y, (A).x ) * (B) )
#define cinv(Z) ( vec2( (Z).x, -(Z).y ) / dot(Z, Z) ) 
#define cdiv(A, B) cmul( A, cinv(B) )

// modified DLMF coloring, adapted from https://www.shadertoy.com/view/WtXGWN
vec3 smooth_dlmf( float h )
{
    vec3 rgb = clamp( vec3(1., -1., -1.) * abs((vec3(8., 4., 8.) * mod(h, 1.0) - vec3(4.5, 1.5, 5.5))) + vec3(-1.5, 1.5, 2.5), 0.0, 1.0 );
	return rgb * rgb * rgb/(1.0 - 3.0 * rgb * (1.0 - rgb));
}

// order and coefficients of base polynomial, constant term first
#define ORDER 3
const float[] P = float[](0.0, -2.0, 0.2, 1.0);

// use Horner's method to evaluate the Newton-Raphson update
vec2 Horner( in vec2 z )
{
    vec2 w = vec2(P[ORDER], 0.0);
    vec2 d = vec2(0.0, 0.0);
    
    for(int k = ORDER - 1; k >= 0; k--)
    {
        d = cmul(d, z) + w;        
        w = cmul(w, z) + vec2(P[k], 0.0);
    }
    
    w += vec2(7.5, 2.0) * cos(0.2 * iTime); // jiggle the constant term a bit for animation
        
    return cdiv(w, d);
}

vec3 NewtonFractal( vec2 z) {
    
    vec2 ld = vec2(0.0);
    float k = 0.0;
    
    for(int i = 0; i < 20; i++) {
        ld = Horner(z);
        if (dot(ld, ld) < 0.001) break;
        z = z - ld;
        k += 1.0;
    }
    
    // multiplier highlights the positions of the roots
    return pow((k + 0.1)/20.0, 0.18) * smooth_dlmf(0.5 * atan(z.y, z.x)/PI);

}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 aspect = iResolution.xy / iResolution.y;
    vec2 z = ( fragCoord.xy / iResolution.y ) - 0.5 * aspect;
    z *= SCALE;
    
    vec3 c = NewtonFractal(z);

    fragColor = vec4(c, 1.0);
}'''


class StNrFractalTransitions(GlslSource):
    name = 'st_nr_fractal_transitions'
    label = 'NR fractal transitions'
    description = "Generate the Newton-Raphson fractal for a general cubic polynomial, using Horner's method."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
