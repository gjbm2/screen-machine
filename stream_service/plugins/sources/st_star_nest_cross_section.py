"""Star Nest Cross Section: trying to understand this fractal

Source: https://www.shadertoy.com/view/MlGfRK
Author: khlorghaal
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Forked by Khlorghaal from Star Nest by Pablo Roman Andrioli https://www.shadertoy.com/view/XlfGRj
// MIT License

#define iterations 64
#define formuparam .5

vec3 subsample(vec2 fc)
{
	vec2 uv= (fc-.5*iResolution.xy)/iResolution.x;
	float time=iTime;
    	
    //uv.x+= time*.1;
    
    vec3 p= vec3(uv, time*.01);
    float pa,a=pa=0.;
    for (int i=0; i<iterations; i++) { 
        p=abs(p)/dot(p,p)-formuparam; // the magic formula
        a+=abs(length(p)-pa); // absolute sum of average change
        pa=length(p);
    }
    a/= float(iterations);
    a= abs(a);
    a= min(1., a);//despeckle
    float lum= pow(a, 8.);
        
	return vec3(lum);	
}
#define SS 6
void mainImage(out vec4 fragColor, in vec2 fragCoord){
    vec2 fc = fragCoord;
    
    vec3 c= vec3(0);
    for(int y=0; y!=SS; y++){
    	for(int x=0; x!=SS; x++){
            c+= subsample(fc + vec2(x,y)/float(SS) );
        }
    }
    c/=float(SS*SS);        
    fragColor= vec4(c,1);
}'''


class StStarNestCrossSection(GlslSource):
    name = 'st_star_nest_cross_section'
    label = 'Star Nest Cross Section'
    description = 'trying to understand this fractal'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
