"""Newton Fractal (Dual Complex): A remix of [url=https://www.shadertoy.com/view/Xd2GzR]this[/url] shader by IQ, adapting it to show the Newton fractal instead of a Mandelbrot.

Source: https://www.shadertoy.com/view/3ltSDS
Author: fizzer
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2013 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// A small remix of IQ's dual complex number Mandelbrot: https://www.shadertoy.com/view/Xd2GzR
//
// Dual complex numbers allow for automatic differentiation of complex number functions
// which is useful for computing a distance estimation. See IQ's shader for more information.
//
// This remix replaces the Mandelbrot orbit with the Newton-Raphson iteration on
// the equation Z³ = 1 which has three roots, and starting values of Z will iterate
// towards one of those roots. More here: https://en.wikipedia.org/wiki/Newton_fractal
//
// The calculated distance is the closest distance to the set of points which are
// stationary under the iteration Z -> Z - (Z³ - 1) / (3Z²)
//


//-------------- dual complex numbers --------------

// complex addition, and derivatives
vec4 dcAdd( vec4 a, vec4 b )
{
    return a + b;
}

// complex multiplication, and derivatives
vec4 dcMul( vec4 a, vec4 b )
{
    return vec4( a.x*b.x - a.y*b.y, 
                a.x*b.y + a.y*b.x,
                a.x*b.z + a.z*b.x - a.y*b.w - a.w*b.y,
                a.x*b.w + a.w*b.x + a.z*b.y + a.y*b.z );
}

// complex squaring, and derivatives
vec4 dcSqr( vec4 a )
{
    return vec4( a.x*a.x - a.y*a.y, 
                2.0*a.x*a.y,
                2.0*(a.x*a.z - a.y*a.w),
                2.0*(a.x*a.w + a.y*a.z) );
}

// Conjugate
vec4 dcConj( vec4 a )
{
    return vec4(a.x, -a.y, a.z, -a.w);
}

// Multiplicative inverse
vec4 dcInverse( vec4 a )
{
    // This can handily be found on the Wikipedia page:
    // https://en.wikipedia.org/wiki/Dual-complex_number
    float n = dot(a.xy, a.xy);
    return vec4(a.x, -a.y, -a.z, -a.w) / n;
}

// Quotient
vec4 dcDiv( vec4 a, vec4 b )
{
    return dcMul(a, dcInverse(b));
}

const vec2 roots[3] = vec2[3](
    vec2(1, 0),
    vec2(-.5, .5 * sqrt(3.)),
    vec2(-.5, -.5 * sqrt(3.))
);

//--------------------------------------------------

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 p = -1.0 + 2.0 * fragCoord.xy / iResolution.xy;
    p.x *= iResolution.x/iResolution.y;

    // animation	
    float tz = 0.5 - 0.5*cos(0.225*iTime);
    float zo = pow( 0.5, 16.0*tz );

    float co = 0.0;

    // Note that I have put the zo scaling factor directly into the
    // starting value of z here, since zo is also the rate of change of the coordinates
    // of the rendered image.
    vec4 z = vec4( .00214*100. + p.x * zo, .001202*100. + p.y * zo, zo, zo );

    for( int i=0; i<256; i++ )
    {
        if(distance(z.xy, roots[0]) < .001 ||
           distance(z.xy, roots[1]) < .001 ||
           distance(z.xy, roots[2]) < .001)
            break;

        // Z -> Z - (Z³ - 1) / (3Z²)        
        z = dcAdd(z, -dcDiv(dcMul(z, dcSqr(z)) - vec4(1, 0, 0, 0), dcMul(vec4(3, 0, 0, 0), dcSqr(z))));

        co += 1.0;
    }

    // Find the closest root for colourpicking.

    float cd = 1e9;
    int ci = 0;
    for(int i = 0; i < 3; ++i)
    {
        float d = distance(z.xy, roots[i]);
        if(d < cd)
        {
            cd = d;
            ci = i;
        }
    }

    // distance	
    // d(c) = |Z|·log|Z|/|Z'|
    float d = 0.0;
    z.xy-=roots[ci];
    if(co<256.) d = -sqrt( dot(z.xy,z.xy)/dot(z.zw,z.zw) )*log(dot(z.xy,z.xy));

    float d2 = d;

    // do some soft coloring based on distance
    d = clamp( d * 1., 0.0, 1. );
    d = pow( d, 1./4. );
    vec3 col = vec3( d );

    col *= ci == 0 ? vec3(1.0, 1., 0.05) : ci == 1 ? vec3(0.0, 1.0, 0.2) : vec3(0.5, 0.0, 1.0);

    col = col * .8 + d * d * .1;
    
    // output color
    fragColor = vec4( pow(col, vec3(1. / 2.2)), 1.0 );
}'''


class StNewtonFractalDualComplex(GlslSource):
    name = 'st_newton_fractal_dual_complex'
    label = 'Newton Fractal (Dual Complex)'
    description = 'A remix of [url=https://www.shadertoy.com/view/Xd2GzR]this[/url] shader by IQ, adapting it to show the Newton fractal instead of a Mandelbrot.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
