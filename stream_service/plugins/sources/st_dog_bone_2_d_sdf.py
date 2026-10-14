"""Dog Bone 2 D SDF: Needed this for a logo, I suppose it could be useful to others as well.

Source: https://www.shadertoy.com/view/wld3D4
Author: BigWIngs
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// "DogBone SDF" by Martijn Steinrucken aka BigWings/CountFrolic - 2019
// The MIT License
// Copyright © 2019 Martijn Steinrucken
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
// Email: countfrolic@gmail.com
// Twitter: @The_ArtOfCode
// YouTube: youtube.com/TheArtOfCodeIsCool
// Facebook: https://www.facebook.com/groups/theartofcode/
//
// I needed something like this for a logo and I think it might
// be useful for other things as well.
// It is made by fitting circles together. The code could probably
// be simpler, especially if you take just the pinch, or just the bulge.
//
// See comments for a slightly more compact version by IQ

float sdDogBone(vec2 p, float w, float r, float b) {
    // p = point to evaluate
    // w = half distance between end points
    // r = radius of endpoints
    // b = bulge -> -1 = pinch to center 0 = straight >0 = bulge out
    
    if(abs(b)<1e-7) b = 1e-7;	// prevent division by 0
    float sb = sign(b);
    
    p = abs(p);
    
    vec2 ep = p-vec2(w, 0);			// end point
    float dE = length(ep)-r;		// distance to end circle
    float y = (w*w-r*r)/(2.*r*b);	// height of center circle
    vec2 cp = vec2(p.x, p.y-y);		// position of center circle
    vec2 ec = sb*(ep-cp);			// vec from end point to center point
    float rc = length(ec)-r*sb;		// radius of center circle
    float dC = sb*(rc-length(cp));	// distance to center circle
    
    return ec.x*ep.y-ec.y*ep.x < 0. ? dE : dC;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-.5*iResolution.xy)/iResolution.y;
	vec2 M = (iMouse.xy-.5*iResolution.xy)/iResolution.xy;
    float t = iTime;
    
    uv *= 2.;
    
    float d = sdDogBone(uv, .75+sin(t)*.25, .25, sin(t));
    
    // coloring from IQ
    vec3 col = vec3(1.0) - sign(d)*vec3(0.1,0.4,0.7);
	col *= 1.0 - exp(-4.0*abs(d));
	col *= 0.7 + 0.2*cos(110.0*d);
	col = mix( col, vec3(1.0), 1.0-smoothstep(0.0,0.015,abs(d)) );
    
    fragColor = vec4(col,1.0);
}'''


class StDogBone2DSdf(GlslSource):
    name = 'st_dog_bone_2_d_sdf'
    label = 'Dog Bone 2 D SDF'
    description = 'Needed this for a logo, I suppose it could be useful to others as well.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
