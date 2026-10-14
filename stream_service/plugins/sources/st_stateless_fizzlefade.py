"""Stateless fizzlefade: Based on the awesome article https://blog.demofox.org/2013/07/06/fast-lightweight-random-shuffle-functionality-fixed/ This is the stateless version adapted for...

Source: https://www.shadertoy.com/view/fslXDB
Author: antovsky
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2021 by Anton Mikhailov
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


/* Any other hash should work according to the article, but I'm sticking to a nice and bijective one just in case... */
uint splitmix32(uint b) {
	b += 0x9e3779b9u;
	b ^= b >> 15;
	b *= 0x85ebca6bu;
	b ^= b >> 13;
	b *= 0xc2b2ae3du;
	b ^= b >> 16;
	return b;
}
/* 
   Adapted into a stateless versionfrom:
   https://blog.demofox.org/2013/07/06/fast-lightweight-random-shuffle-functionality-fixed/
   https://github.com/Atrix256/RandomCode/blob/master/StoragelessShuffle/Source.cpp
*/
uint shuffle(uint idx, uint count, uint seed) {
	/* Pre-calculate the masks needed for the Feistel network. */
	/* If doing this for many indecies in a row, it would make sense to cache this. */
	uint next_pow_4 = 4u;
	while (count > next_pow_4)
		next_pow_4 *= 4u;
	uint num_bits = 0u;
	uint mask = next_pow_4 - 1u;
	while (mask != 0u) {
		mask = mask >> 1;
		num_bits++;
	}
	uint half_num_bits = num_bits / 2u;
	uint right_mask = (1u << half_num_bits) - 1u;
	uint left_mask = right_mask << half_num_bits;

	/* This will terminate as long as idx starts less than count. */
	while (true)
	{
		/* Split the index. */
		uint left = (idx & left_mask) >> half_num_bits;
		uint right = (idx & right_mask);
		/* Do 4 Feistel rounds. */
		for (int index = 0; index < 4; ++index)
		{
			uint newLeft = right;
			uint newRight = left ^ (splitmix32(right ^ seed) & right_mask);
			left = newLeft;
			right = newRight;
		}
		/* Re-assemble the bits into a shuffled index. */
		idx = (left << half_num_bits) | right;

		/* If it's in range, we are done. Otherwise keep trying (reject this result) */
		if (idx < count)
			return idx;
	}
	/* We should never get here. */
	return 0u;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{

    vec3 col = vec3(1.0);
    
    // TEH MEAT: Stateless fizzlefade effect
    float t = abs(1.0-fract(iTime*0.0125)*2.0); // sawtooth
    uint idx = uint(fragCoord.x + fragCoord.y * iResolution.x);
    uint count = uint(iResolution.x * iResolution.y);
    uint i = uint(float(count) * t);
    uint seed = 108u;
    if (shuffle(idx, count, seed) >= i)
        col = vec3(0.0);
    // END MEAT
     
    // Output to screen
    fragColor = vec4(col,1.0);
}'''


class StStatelessFizzlefade(GlslSource):
    name = 'st_stateless_fizzlefade'
    label = 'Stateless fizzlefade'
    description = 'Based on the awesome article https://blog.demofox.org/2013/07/06/fast-lightweight-random-shuffle-functionality-fixed/ This is the stateless version adapted for...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
