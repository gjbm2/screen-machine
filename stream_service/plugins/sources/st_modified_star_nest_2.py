"""Modified Star Nest 2: Original: Star Nest by Pablo Andrioli (user “Kali” on Shadertoy). https://www.shadertoy.com/view/XlfGRj Star Nest by Pablo Andrioli (user “Kali” on Shadertoy)....

Source: https://www.shadertoy.com/view/ltVfW3
Author: RaduBT
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // This content is under the MIT License.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Star Nest by Pablo Roman Andrioli

// This content is under the MIT License.

#define iterations 20
#define formuparam 0.53

#define volsteps 20
#define stepsize 0.1

#define zoom   0.400
#define tile   0.850
#define speed  0.0150

#define brightness 0.0055
#define darkmatter 0.7300*fft
#define distfading 0.730
#define saturation 0.850


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    
    vec2 pr = fragCoord.xy/iResolution.xy;

    vec3 col = texture( iChannel0, pr ).xyz;
    //vec3 col = texelFetch( iChannel0, ivec2(fragCoord-0.5), 0 ).xyz;
    
    col *= 0.5 + 0.5*pow( 16.0*pr.x*pr.y*(1.0-pr.x)*(1.0-pr.y), 0.05 );
    
    float fft = (((texture( iChannel0, vec2(pr.y+1.0, 0.0) ).y)) * 3.0);
    
	//get coords and direction
	vec2 uv=fragCoord.xy/iResolution.xy-.9;
	uv.y*=iResolution.y/iResolution.x;
	vec3 dir=vec3(uv*zoom,0.51);
	float time=iTime*speed+0.25;

	//mouse rotation
	float a1=.5+iMouse.x/iResolution.x*.03;
	float a2=.8+iMouse.y/iResolution.y*.03;
	mat2 rot1=mat2(cos(a1),sin(a1),-sin(a1),cos(a1));
	mat2 rot2=mat2(cos(a2),sin(a2),-sin(a2),cos(a2));
	dir.xz*=rot1;
	dir.xy*=rot2;
	vec3 from=vec3(1.,.5,0.5);
	from+=vec3(time*2.,time,-2.);
	from.xz*=rot1;
	from.xy*=rot2;
	
	//volumetric rendering
	float s=0.01,fade=1.;
	vec3 v=vec3(0.);
	for (int r=0; r<volsteps; r++) {
		vec3 p=from+s*dir*.5;
		p = abs(vec3(tile)-mod(p,vec3(tile*2.))); // tiling fold
		float pa,a=pa=0.;
		for (int i=0; i<iterations; i++) { 
			p=abs(p)/dot(p,p)-formuparam; // the magic formula
			a+=abs(length(p)-pa); // absolute sum of average change
			pa=length(p);
		}
		float dm=max(0.,darkmatter-a*a*.001); //dark matter
		a*=a*a; // add contrast
		if (r>6) fade*=1.-dm; // dark matter, don't render near
		//v+=vec3(dm,dm*.5,0.);
		v+=fade;
		v+=vec3(s,s*s,s*s*s*s)*a*brightness*fade; // coloring based on distance
		fade*=distfading; // distance fading
		s+=stepsize;
	}
	v=mix(vec3(length(v)),v,saturation); //color adjust
	fragColor = vec4(v*.01,1.);	
	
}'''


class StModifiedStarNest2(GlslSource):
    name = 'st_modified_star_nest_2'
    label = 'Modified Star Nest 2'
    description = 'Original: Star Nest by Pablo Andrioli (user “Kali” on Shadertoy). https://www.shadertoy.com/view/XlfGRj Star Nest by Pablo Andrioli (user “Kali” on Shadertoy)....'
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'music'}
    params = scene_params({}, render_scale=0.4)
