"""Julia   Triangle Inequality: Triangle Inequality coloring, designed by Damien M. Jones and Javier Barrallo. This is an adaptation of code I wrote in 2001. Unfortunately it only works in...

Source: https://www.shadertoy.com/view/wlXyDX
Author: iq
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Triangle Inequality coloring. This is an adaptation of
// code I wrote in 2001. The algorihtm was designed by 
// Damien M. Jones and Javier Barrallo who were trying to
// measure the average orbit curvature.
//
// The algorithm requires double precision floating point
// numbers, which GLSL doesn't support. See line 32. So
// this image you see here is just an approximation to the
// real image, which should have way longer hairs going out
// all the way to the border of the image.


#define AA 2

vec3 render( vec2 p, float time )
{
    // animation
	float ltime = 0.5-0.5*cos(time*0.06);
    float zoom = pow( 0.9, 40.0*ltime );
	vec2  cen = vec2( 0.2655,0.301 ) + zoom*0.8*cos(4.0+2.0*ltime);
	vec2 c = vec2( -0.745, 0.186 ) - 0.045*zoom*(1.0-ltime*0.5);

    // pixel to z plane
    p = (2.0*p-iResolution.xy)/iResolution.y;
	vec2 zo = cen + (p-cen)*zoom;
    
    // iteration
    float th  = 1e7; // th needs to be way bigger to work well, but in
    float ld2 = 1.0; // WebGL we only have floats, so it that sucks!
    const int kNumIterations = 2048;
    vec2  z = zo;
    float lz2 = dot(z,z);
    float ti_av1 = 0.0;
    float ti_av2 = 0.0;
	float ti_lc = length(c);
    float n = 0.0;
    for( int i=0; i<kNumIterations; i++ )
	{
        // z' = 2·z·z', derivative of z for distance computation
        ld2 *= 4.0*lz2; 
        
        // z = z²+c
        z = vec2(z.x*z.x-z.y*z.y, 2.0*z.x*z.y) + c;
        
        // escape condition
		float olz2 = lz2;
        lz2 = dot(z,z);
		if( lz2>th*th ) break;
        
        // triangle inequality
        if( i>0 )
        {
        	ti_av2 = ti_av1;
            float mi = abs(olz2 - ti_lc);
            float ma = olz2 + ti_lc;
            ti_av1 += (sqrt(lz2)-mi)/(ma-mi);
        }
        
        // iteration counter
        n += 1.0;
	}
    
    // --- properties ---
    
    // continuous iteration : https://iquilezles.org/www/articles/mset_smooth/mset_smooth.htm
    float f = log2(log(length(z))/log(th));
    float sn = n - f;
    
    // distance: https://iquilezles.org/www/articles/distancefractals/distancefractals.htm
    float d = sqrt(lz2/ld2)*log(lz2);

    // triangle inequality
	ti_av1 = ti_av1/(n    );
    ti_av2 = ti_av2/(n-1.0);
	ti_av1 = ti_av2+(ti_av1-ti_av2)*smoothstep(0.0,1.0,1.0-f);
    
    // --- coloring ---
    
    vec3 col = max(0.6+0.8*sin(ti_av1*1.5 + 5.5 + vec3(0.0,0.3,0.5)),0.0);
    col *= clamp( 1024.0*d/zoom, 0.0, 1.0 );
        
    return col;
}
	
void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 col = vec3(0.0);
	for( int j=0; j<AA; j++ )
	for( int i=0; i<AA; i++ )
	{
		vec2 of = vec2(float(i),float(j))/float(AA)-0.5;
	    col += render( fragCoord+of, iTime );
	}
	col /= float(AA*AA);
	
	fragColor = vec4( col, 1.0 );
}'''


class StJuliaTriangleInequality(GlslSource):
    name = 'st_julia_triangle_inequality'
    label = 'Julia   Triangle Inequality'
    description = 'Triangle Inequality coloring, designed by Damien M. Jones and Javier Barrallo. This is an adaptation of code I wrote in 2001. Unfortunately it only works in...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.35)
