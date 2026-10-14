"""Thick orbits: test

Source: https://www.shadertoy.com/view/lsBSW3
Author: guil
License: public-domain
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: /* public domain */
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/* public domain */

#define N 40


vec2 cinv( vec2 z)  { float d = dot(z,z); return vec2( z.x, -z.y ) / d; }
vec2 csqr( vec2 a ) { return vec2(a.x*a.x-a.y*a.y, 2.0*a.x*a.y ); }

vec2 p;
vec2 f( vec2 x ){float t = cos(0.1*iTime);return csqr(x+t*cinv(x))+p;}
 

void mainImage( out vec4 fragColor, in vec2 fragCoord ) {
	p = 2.*(-iResolution.xy+2.0*fragCoord.xy)/iResolution.y;
	vec2 z=p;
	float zoom = 1.2;
	float thick = 0.30;
	for ( int i = 0; i < N; i++ ){
		z = f(z);
		float ot = abs(dot(z+p,cinv(z-p)));
		if ( ot > 1.0 ){
			ot = 1.0/ot;
			z *=  ot;			
		}
		if ( ot > 1.0-thick ){
			float c = float(i)/float(N)*5.0;
			vec3 color = vec3(cos(c*1.0), cos(c*2.0), cos(c*4.0))*.5+.5;
			float shade = 1.0-(1.0-ot)/thick;
			fragColor = vec4( color*shade, 1.0 );
			return;
		}
		z *= zoom;
        
	}
	fragColor = vec4( 0.0,0.0,0.0, 1.0 );
 
}'''


class StThickOrbits(GlslSource):
    name = 'st_thick_orbits'
    label = 'Thick orbits'
    description = 'test'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
