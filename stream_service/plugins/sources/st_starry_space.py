"""Starry Space: Dynamic fractal - treats iterated point as particle with inertia. Can give some really interesting variations in look. Uncomment DYNAMICS_ON_MOUSE to explore!

Source: https://www.shadertoy.com/view/ll33zN
Author: huwb
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // This content is under the MIT License.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Started as Star Nest by Pablo RomÃ¡n Andrioli
// AA modifications by Beibei Wang and Huw Bowles (https://www.shadertoy.com/view/XllGzN)

// This shader is an idea i have been meaning to try out for a while which I'll
// call a "dynamic fractal".

// The idea is simple - treat the iteration point as a particle with inertia (non-zero mass).
// In each iteration compute a new target point for the particle using the fractal formula and
// then apply a force to the particle to push it towards this position.

// This can change the appearance of the result significantly.

// Uncomment this define to set the sprint constants with the mouse
//#define DYNAMICS_ON_MOUSE

// This content is under the MIT License.


#define iterations 17
#define formuparam 0.53

#define volsteps 18
#define stepsize 0.050

#define zoom   0.800
#define tile   0.850
#define speed  0.10 

#define brightness 0.0015
#define darkmatter 0.300
#define distfading 0.760
#define saturation 0.800


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	//get coords and direction
	vec2 uv=fragCoord.xy/iResolution.xy-.5;
	uv.y*=iResolution.y/iResolution.x;
	vec3 dir=vec3(uv*zoom,1.);
	float time=(iTime-3308.)*speed;

	
	vec3 from=vec3(1.,.5,0.5);
	
	
	vec3 forward = vec3(0.,0.,1.);
	
	//mouse rotation
	float a1 = 0.3;//3.1415926 * (iMouse.x/iResolution.x-.5);
	mat2 rot1 = mat2(cos(a1),sin(a1),-sin(a1),cos(a1));
	float a2 = .6;//3.1415926 * (iMouse.y/iResolution.y-.5);
	mat2 rot2 = mat2(cos(a2),sin(a2),-sin(a2),cos(a2));
	dir.xz*=rot1;
	forward.xz *= rot1;
	dir.yz*=rot1;
	forward.yz *= rot1;

	// pan (dodgy)
    float panmul = 1.;
    #ifdef DYNAMICS_ON_MOUSE
    panmul = 0.;
	#endif
	from += (panmul * iMouse.x/iResolution.x-.5)*vec3(-forward.z,0.,forward.x);
    
	//zoom
	float zooom = time;
	from += forward* zooom;
	float sampleShift = mod( zooom, stepsize );
	float zoffset = -sampleShift;
	sampleShift /= stepsize; // make from 0 to 1
	
    // dynamics - proportional and differential gains (i.e. spring and damping constants)
    float kp = 1.36;
    float kv = .9;
    #ifdef DYNAMICS_ON_MOUSE
    kp = 2.*iMouse.x/iResolution.x;
    kv = 2.*iMouse.y/iResolution.y;
    #endif
    
	//volumetric rendering
	float s=0.1;
	vec3 v=vec3(0.);
	for (int r=0; r<volsteps; r++) {
		vec3 p=from+(s+zoffset)*dir;// + vec3(0.,0.,zoffset);
		p = abs(vec3(tile)-mod(p,vec3(tile*2.))); // tiling fold
		float pa,a=pa=0.;
        vec3 pv = vec3(0.);
		for (int i=0; i<iterations; i++) { 
			vec3 newp=abs(p)/dot(p,p)-formuparam; // the magic formula
            // update particle vel
            pv += (newp-p)*kp + (-pv)*kv;
            // update particle pos
            p += pv;
            float D = abs(length(p)-pa); // absolute sum of average change
            a += i > 7 ? min( 12., D) : D;
			pa=length(p);
		}
		//float dm=max(0.,darkmatter-a*a*.001); //dark matter
		a*=a*a; // add contrast
		//if (r>3) fade*=1.-dm; // dark matter, don't render near
		// brightens stuff up a bit
		float s1 = s+zoffset;
		// need closed form expression for this, now that we shift samples
		float fade = pow(distfading,max(0.,float(r)-sampleShift));
		v+=fade;
        
		// fade out samples as they approach the camera
		if( r == 0 )
			fade *= 1. - sampleShift;
		// fade in samples as they approach from the distance
		if( r == volsteps-1 )
			fade *= sampleShift;
		v+=vec3(2.*s1,4.*s1*s1,16.*s1*s1*s1*s1)*a*brightness*fade; // coloring based on distance
		s+=stepsize;
	}
	v=mix(vec3(length(v)),v,saturation); //color adjust
	fragColor = vec4(v*.005,1.);	
}
'''


class StStarrySpace(GlslSource):
    name = 'st_starry_space'
    label = 'Starry Space'
    description = 'Dynamic fractal - treats iterated point as particle with inertia. Can give some really interesting variations in look. Uncomment DYNAMICS_ON_MOUSE to explore!'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.35)
