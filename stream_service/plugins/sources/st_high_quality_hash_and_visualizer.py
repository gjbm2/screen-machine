"""High Quality Hash and Visualizer: First a 32 bit hash value is generated. Left Side shows that hash value converted to a float Right Side shows each bit of that hash value individually. the...

Source: https://www.shadertoy.com/view/MsGfRz
Author: James_Harnett
License: Unlicense
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // LICENSE: http://unlicense.org/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// LICENSE: http://unlicense.org/
#define M1 1597334677U     //1719413*929
#define M2 3812015801U     //140473*2467*11  is also first 32bits of M1*M1

uint hash(uvec2 q)
{
	q ^= uvec2(M1, M2);
    uint n = q.x * q.y;
    n ^= n >> 15;
    n *= (q.x + q.y);
    return n;
}

float hashFloat( uvec2 q )
{
    return float(hash(q)) * (1.0/float(0xffffffffU));
}

float hashBit( uvec2 q,int bit)
{
    return float((hash(q)&(1U<<bit))!=0U); 
}


void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    uvec2 p = uvec2(fragCoord)+uint(2*iFrame);
	int center = int(fragCoord.x) - (int(iResolution.x)>>1);
	float c = 0.0;
    
   	if(center < 0)c = hashFloat(p);
    if(center > 0)c = hashBit(p,iFrame>>5);
    
    fragColor = vec4(c,c,c,1.0);
}'''


class StHighQualityHashAndVisualizer(GlslSource):
    name = 'st_high_quality_hash_and_visualizer'
    label = 'High Quality Hash and Visualizer'
    description = 'First a 32 bit hash value is generated. Left Side shows that hash value converted to a float Right Side shows each bit of that hash value individually. the...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
