"""Perlin noise fire: Perlin noise based fire; each octave of the Perlin noise has a slightly different speed than the others, y ~ f(log2(octive_scale)*time)

Source: https://www.shadertoy.com/view/MsdyDN
Author: BenWheatley
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: * License MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
 * "Fire" by Ben Wheatley - 2018
 * License MIT License
 * Contact: github.com/BenWheatley
 */

// constants
const int MAX_OCTAVE = 8;
const float PI = 3.14159265359;

float cosineInterpolate(float a, float b, float x) {
	float ft = x * PI;
	float f = (1.0 - cos(ft)) * 0.5;
	
	return a*(1.0-f) + b*f;
}

float seededRandom(float seed) {
    int x = int(seed);
    x = x << 13 ^ x;
    x = (x * (x * x * 15731 + 789221) + 1376312589);
    x = x & 0x7fffffff;
    return float(x)/1073741824.0;
}

// The magic constants are essentially arbitary:
// they define the scale of the largest component of the Perlin noise
float perlinNoise(float x_arg, float y_arg, float time_arg) {
    float sum = 0.0;
    for (int octave=0; octave<MAX_OCTAVE; ++octave) {
        float sf = pow(2.0, float(octave));
        float x = x_arg*sf;
    	float y = (y_arg*sf) + (1.5*time_arg*log2(sf));
    	float y_scale = 1.0*sf;
        
		float x_floor = floor(x);
		float y_floor = floor(y);
		float fraction_x = x - x_floor;
		float fraction_y = y - y_floor;
        
        float t1 = seededRandom( x_floor	+	y_scale *  y_floor      );
		float t2 = seededRandom( x_floor	+	y_scale * (y_floor+1.0) );
        
        x_floor += 1.0;
        float t3 = seededRandom( x_floor	+	y_scale *  y_floor      );
		float t4 = seededRandom( x_floor	+	y_scale * (y_floor+1.0) );
        
		float i1 = cosineInterpolate(t1, t2, fraction_y);
		float i2 = cosineInterpolate(t3, t4, fraction_y);
        
        sum += cosineInterpolate(i1, i2, fraction_x)/sf;
    }
    return 2.0*sum;
}

// main
void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
    vec2 uv = fragCoord.xy / iResolution.xy;
    
    float dx = 0.5 - uv.x;
    float dy = 0.5 - uv.y;
    dy *= iResolution.y / iResolution.x;
    
    float c = perlinNoise(dx, dy, iTime);
    // Fiddle with brightness and contrast to push it into a nice range
    c -= 2.4;
    c *= cos(dx*PI); // This also makes it central in the image
    c *= 1.0-((uv.y/iResolution.y)*256.0);
    
    float red = c*0.9;
    float green =  c*(dy+0.25); // This makes it more yellow towards the bottom
    
    fragColor = vec4(red, green, 0, 0);
}'''


class StPerlinNoiseFire(GlslSource):
    name = 'st_perlin_noise_fire'
    label = 'Perlin noise fire'
    description = 'Perlin noise based fire; each octave of the Perlin noise has a slightly different speed than the others, y ~ f(log2(octive_scale)*time)'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
