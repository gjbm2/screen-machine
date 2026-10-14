"""Simiancraft Nebula v2: Another test for the simiancraft.com backdrop but with the values greatly increased

Source: https://www.shadertoy.com/view/WltSWB
Author: the_simian
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // MIT lisence
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// SIMIANCRAFT Deadlights V2 'Deadlights nebula'
// backdrop animation for https://simiancraft.com/
// Jesse Harlin, 2020
// MIT lisence
// Exactly like the last one but cranked way way up with changes to speed/glow etc.

#define STARFIELD_LAYERS_COUNT 12.0

float PI = 3.1415;
float MIN_DIVIDE = 64.0;
float MAX_DIVIDE = .01;


mat2 Rotate(float angle){
	float s = sin(angle);
	float c = cos(angle);
	return mat2(c,-s, s, c); //this rotates the thing.
}

float Star(vec2 uv, float flaresize, float rotAngle, float randomN){

	float d = length(uv);
	//Star core
	float starcore = 0.05/d;
	uv *= Rotate(-2.0 * PI * rotAngle);
	float flareMax = 1.0;

	//flares
	float starflares = max(0.0, flareMax - abs(uv.x * uv.y * 3000.0));
	starcore += starflares * flaresize;
	uv *= Rotate(PI * 0.25);
	starflares = max(0.0, flareMax - abs(uv.x * uv.y * 3000.0));
	starcore += starflares * 0.3 * flaresize;
	//light can't go forever, fade it concentrically.
	starcore *= smoothstep(1.0, 0.05, d);
	return starcore;
}

float PseudoRandomizer(vec2 p){
	//its not really random, but it looks random.
	p = fract(p*vec2(123.45, 345.67));
	p+= dot(p, p+45.32);
	return (fract(p.x * p.y));
}

vec3 StarFieldLayer(vec2 uv, float rotAngle){
	vec3 col = vec3(0);

	vec2 gv = fract(uv) -0.5;
	vec2 id = floor(uv);
	
	float deltaTimeTwinkle = iTime * 0.35;

	// this loop goes over the neighbors and includes adjacent cell information.
	// so the stars are not 'clipped'.
	for (int y = -1; y <= 1; y++) {
		for (int x = -1; x <= 1; x++) {
			vec2 offset = vec2(x, y);

			//randomize X and Y
			float randomN = PseudoRandomizer(id + offset); // rand in 0 to 1.
			float randoX = randomN - 0.5;
			float randoY = fract(randomN * 45.0) - 0.5;
			vec2 randomPosition = gv - offset - vec2(randoX, randoY);
			//uses the fract 'trick' to get random sizes
			float size = fract(randomN * 1356.33);
			float flareSwitch = smoothstep(0.9, 1.0, size);
			//the actual star.
			float star = Star(randomPosition, flareSwitch, rotAngle, randomN);
			
			//fract trick random colors.
			float randomStarColorSeed = fract(randomN * 2150.0) * (3.0 * PI) * deltaTimeTwinkle;
			vec3 color = sin(vec3(0.7, 0.3, 0.9) * randomStarColorSeed);

			//compress
			color = color * (0.4 * sin(deltaTimeTwinkle)) + 0.6;
			//filter
			color = color * vec3(1, 0.1,  0.9 + size);
			float dimByDensity = 15.0/STARFIELD_LAYERS_COUNT;
			col += star * size * color * dimByDensity;
		}
	}

	return col;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{

    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;
	
	//Normalize the mouse also
	//vec2 Mouser = (iMouse.xy - iResolution.xy * 0.5)/iResolution.y;
	float deltaTime = iTime * 0.01;


	//uv += uv + vec2(3.1, 0);
    vec3 col = vec3(0.0);
	
    float rotAngle = deltaTime * 0.09;
	

	for(float i=0.0; i < 1.0; i += (1.0/STARFIELD_LAYERS_COUNT)){
		float layerDepth = fract(i + deltaTime);
		float layerScale = mix(MIN_DIVIDE,MAX_DIVIDE,layerDepth);
		float layerFader = layerDepth * smoothstep(0.1, 1.1, layerDepth);
		float layerOffset = i * (3430.00 + fract(i));
		mat2 layerRot = Rotate(rotAngle * i * -10.0);
		uv *= layerRot;
		vec2 starfieldUv = uv * layerScale + layerOffset;
		col += StarFieldLayer(starfieldUv, rotAngle) * layerFader;
	}

	
    // Output to screen
    fragColor = vec4(col,1.0);
}'''


class StSimiancraftNebulaV2(GlslSource):
    name = 'st_simiancraft_nebula_v2'
    label = 'Simiancraft Nebula v2'
    description = 'Another test for the simiancraft.com backdrop but with the values greatly increased'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.35)
