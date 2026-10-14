"""Volumetric particles (186 c): Random white dots -> it is not a bug, it is a feature :)

Source: https://www.shadertoy.com/view/llBcWR
Author: PrzemyslawZaworski
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License https://opensource.org/licenses/MIT
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License https://opensource.org/licenses/MIT
// Copyright © 2017 Przemyslaw Zaworski
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
    
#define s for (int i = 0; i ++< 50;) 
void mainImage(out vec4 c, vec2 f) {											\
s
{
    c.w+=.1;
	vec3 p=c.w*vec3(f/iResolution.xy,2);p.y+=.2*iTime;
	s p = vec3(1.25,1.07,1.29)*abs(p/dot(p,p)-vec3(.95,.91,.67));
	c.xyz+=p/50.;
}}'''


class StVolumetricParticles186C(GlslSource):
    name = 'st_volumetric_particles_186_c'
    label = 'Volumetric particles (186 c)'
    description = 'Random white dots -> it is not a bug, it is a feature :)'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
