"""Dancing Sinus: Three sinusoids dance on the spectrum of music. Inspired by https://github.com/overtone/shadertone/blob/master/examples/disco.glsl

Source: https://www.shadertoy.com/view/XdtBR2
Author: FraKtus
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2018 Marco Hinic
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
//
// Original https://github.com/overtone/shadertone/blob/master/examples/disco.glsl
//

float hz(float hz)
{
    float u = hz/11000.0;
    return texture(iChannel0,vec2(u,0.25)).x;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = fragCoord.xy / iResolution.xy;

    // 3 dancing magenta, cyan & yellow sine waves
    float v1 = 0.02 + 0.4*hz(100.0);
    float v2 = 0.02 + 0.4*hz(500.0);
    float v3 = 0.02 + 0.4*hz(2000.0);
    vec3 col = vec3(0.0, 0.0, 0.0);
    float v1x = uv.x - 0.5 + sin(5.0*iTime + 1.5*uv.y)*v1;
    float v2x = uv.x - 0.2 + sin(3.0*iTime + 0.8*uv.y)*v2;
    float v3x = uv.x - 0.3 + sin(7.0*iTime + 3.2*uv.y)*v3;
    col += vec3(1.0,0.0,1.0) * abs(0.066/v1x) * v1;
    col += vec3(1.0,1.0,0.0) * abs(0.066/v2x) * v2;
    col += vec3(0.0,1.0,1.0) * abs(0.066/v3x) * v3;

    // with a lighted disco floor pattern
    float uvy2 = 0.4*iTime-uv.y;
    float a1 = max(0.0,0.25*hz(200.0)) *
        max(0.0,min(1.0,sin(50.0*uv.x)*sin(50.0*uvy2)));
    col += vec3(1.0,1.0,1.0) * a1;

    fragColor = vec4(col,1.0);
}'''


class StDancingSinus(GlslSource):
    name = 'st_dancing_sinus'
    label = 'Dancing Sinus'
    description = 'Three sinusoids dance on the spectrum of music. Inspired by https://github.com/overtone/shadertone/blob/master/examples/disco.glsl'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.5)
