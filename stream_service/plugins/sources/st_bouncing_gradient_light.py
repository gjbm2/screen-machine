"""Bouncing gradient light: Playing around with light coming from the side.

Source: https://www.shadertoy.com/view/ttfBD2
Author: pixeljig
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: MIT LICENSE
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
MIT LICENSE
Copyright 2020 Ahmed Harmouche
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
*/
void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = fragCoord/iResolution.xy;
    
    float lightPos = 1.1;
    
    float dist = 1.0/(lightPos - uv.x);
    
    dist *= 0.1;
    
    float dist1 = dist;
    
    dist1 = pow(dist1, 0.5);
    
    vec3 col = 0.5 + 0.5*cos(iTime+uv.xyx+vec3(0,2,4));

    vec3 outCol = vec3(mix(col*dist, col*dist1, sin(iTime*3.0)));
                       
    fragColor = vec4(outCol, 1.0);
}'''


class StBouncingGradientLight(GlslSource):
    name = 'st_bouncing_gradient_light'
    label = 'Bouncing gradient light'
    description = 'Playing around with light coming from the side.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
