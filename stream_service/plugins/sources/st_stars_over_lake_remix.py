"""Stars over Lake (remix): Stars from https://www.shadertoy.com/view/XlfGRj Mountains & trees from https://www.shadertoy.com/view/4lKcDD Click and drag to recompose starfield. I'd like...

Source: https://www.shadertoy.com/view/ttBGDd
Author: mosaic
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // This content is under the MIT License.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Contains code from "Star Nest" by Pablo Roman Andrioli

// This content is under the MIT License.

#define iterations 17
#define formuparam 0.53

#define volsteps 20
#define stepsize 0.1

#define zoom   0.800
#define tile   0.850
#define speed  0.005 

#define brightness 0.0015
#define darkmatter 0.300
#define distfading 0.730
#define saturation 0.850

#define PI (4.0 * atan(1.0))
#define TWO_PI PI*2.


#define HASHSCALE1 443.8975

float hash11(float p) {
    vec3 p3 = fract(vec3(p) * HASHSCALE1);
    p3 += dot(p3, p3.yzx + 19.19);
    return fract((p3.x + p3.y) * p3.z);
}

float lerp(float a, float b, float t) {
    return a + t * (b - a);
}

float noise(float p) {
    float i = floor(p);
    float f = fract(p);
    float t = f * f * (3.0 - 2.0 * f);
    return lerp(f * hash11(i), (f - 1.0) * hash11(i + 1.0), t);
}
float fbm(float x, float persistence, int octaves) {
    float total = 0.0;
    float maxValue = 0.0;
    float amplitude = 1.0;
    float frequency = 1.0;
    for (int i = 0; i < octaves; ++i) {
        total += noise(x * frequency) * amplitude;
        maxValue += amplitude;
        amplitude *= persistence;
        frequency *= 2.0;
    }
    return (total / maxValue);
}

float msine2(vec2 uv) {
    return (fbm(uv.x / 10., 0.25, 4)*20. + 0.5);
}


float xRandom(float x) {
    return mod(x * 7241.6465 + 2130.465521, 64.984131);
}


float shape2(vec2 uv, int N, float radius_in, float radius_out, float zoom3) {
    float color = 0.0;
    float d = 0.0;
    float a = atan(uv.x, uv.y) + PI;
    float rx = TWO_PI / float(N);
    d = cos(floor(.5 + a / rx) * rx - a) * length(uv);
    color = smoothstep(.44, .44 + (2. + 1.2 * zoom3) / iResolution.y, abs(d - radius_in) + radius_out);
    return (1. - color);
}

float trees2(vec2 uv) {
    float zoom1 = 10.;
    uv.x += .2;
    uv *= zoom1;
    uv.y+=1.0;
    uv.y=-uv.y;
    vec2 tuvy = vec2(0., 0.2 * msine2(vec2(floor(uv.x / 0.38), uv.y)));
    float rval = xRandom(floor(uv.x / 0.38));
    float d = 0.;
    if (rval > (55.) * fract(cos(rval)) + 85. * (sin(rval))) {
        rval = max(1.5, 2.5 * abs(sin(rval)));
        uv.x = mod(uv.x, 0.38) - 0.19;
        uv += tuvy;
        uv *= rval;
        float xval = -1.2 * sin(xRandom(tuvy.y))*0.19 * (1.25 - rval);
        uv.y += -0.25 / rval;
        uv.x += xval;
        vec2 ouv = uv;
        uv.y *= .85;
        d = shape2(uv, int(3.), -0.380, 0., zoom + rval);
        uv.y += .12;
        uv.y *= .75;
        d = max(d, shape2(uv, int(3.), -0.370, 0., zoom + rval));
        uv.y += .1;
        uv.y *= 1.2;
        d = max(d, shape2(uv, int(3.), -0.3650, 0., zoom + rval));
        d = max(d, smoothstep(0.02 + (2. + 1.2 * (zoom + rval)) / iResolution.y, 0.02, abs(uv.x)) * step(ouv.y, 0.)) *
                step(-0.75 + 0.12 * (2.5 - rval), ouv.y);
    }
    return d;
}


float msine(vec2 uv) {
    float heightA = 0.025;
    float heightB = 0.025;
    float heightC = 0.013; //+0.071*sin(iTime/105.); //xD
    uv.y = sin((uv.x + (1.))*5.0) * heightA;
    uv.y = uv.y + sin((uv.x + (0. / 5.))*3.0) * heightB;
    uv.y = uv.y + sin((uv.x + (1.))*2.0) * heightC;
    return uv.y;
}

float layer_bghills(vec2 uv) {
      //uv.y += 1.2;
 //uv.y-=0.5;
     uv.x += 0.;
    float d = smoothstep(5.5 + 20. / iResolution.y, 5.5, msine2(uv / 0.38 - 0.038)*40. + uv.y * 40. + 010.6);
    d = smoothstep(-0.1555 + 8. / iResolution.y, -0.1555, .2 * msine2((uv * 10.) / 0.38 - 0.038) + uv.y * 11. + 0.86);
    return d- trees2(uv);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	//get coords and direction
	vec2 uv=fragCoord.xy/iResolution.xy-.5;
	//uv.y*=iResolution.y/iResolution.x;
    
    uv.y=abs(uv.y);
    
    vec2 res = iResolution.xy / iResolution.y;
     uv = (fragCoord.xy) / iResolution.y - res / 2.0;
    uv.y+=0.09;
    if (uv.y<0.) uv.x +=0.01*sin(100.*uv.y+iTime);//line from hamtarodeluxe
    uv.y=-abs(uv.y);
    
	vec3 dir=vec3(uv*zoom,1.);
	float time=iTime*speed+.25;

	//mouse rotation
	float a1=.5+iMouse.x/iResolution.x/20.;
	float a2=.8+iMouse.y/iResolution.y/20.;
	mat2 rot1=mat2(cos(a1),sin(a1),-sin(a1),cos(a1));
	mat2 rot2=mat2(cos(a2),sin(a2),-sin(a2),cos(a2));
	dir.xz*=rot1;
	dir.xy*=rot2;
	vec3 from=vec3(1.,.5,0.5);
	from+=vec3(time*2.,time,-2.);
	from.xz*=rot1;
	from.xy*=rot2;
	
	//volumetric rendering
	float s=0.1,fade=1.;
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
	v=layer_bghills(uv)*mix(vec3(length(v)),v,saturation); //color adjust
	fragColor = vec4(v*.01,1.);	
   //fragColor=vec4(trees2(uv));
	
}'''


class StStarsOverLakeRemix(GlslSource):
    name = 'st_stars_over_lake_remix'
    label = 'Stars over Lake (remix)'
    description = "Stars from https://www.shadertoy.com/view/XlfGRj Mountains & trees from https://www.shadertoy.com/view/4lKcDD Click and drag to recompose starfield. I'd like..."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.4)
