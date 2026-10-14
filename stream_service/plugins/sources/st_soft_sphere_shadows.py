"""Soft Sphere Shadows: Trying to get correct shadows from spherical lights/occluders. I'm sure someone with an actual education could do it much more elegantly, but I'm pleased with...

Source: https://www.shadertoy.com/view/4d2SWV
Author: ben
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Creative Commons CC0 1.0 Universal (CC-0)
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//	Creative Commons CC0 1.0 Universal (CC-0) 
//	
//	Analytic shadows from spherical occluders and a spherical light.
//	
//	It breaks if the light and occluder intersect, but I think all other cases are handled correctly.
//	
//	~bj.2014
//	

#define POST_TONEMAP
#define POST_DITHER
#define POST_VIGNETTE

const float PI = 3.1415927;
const int nSpheres	= 8;
const float camDist	= 32.0;

struct sphere { vec3 o; float r; int id; };
struct ray { vec3 o, d; float t; sphere s; };

vec2 hash(float n) { return fract(sin(vec2(n,n+1.0))*vec2(43758.5453123,22578.1459123)); }
float hash(vec2 p) { return fract(1e4*sin(17.0*p.x+p.y*0.1)*(0.1+abs(sin(p.y*13.0+p.x)))); }

float shadow(vec3 P, vec3 lightPos, float lightRad, vec3 occluderPos, float occluderRad)
{
	float radA = lightRad;
	float radB = occluderRad;
	
	vec3 vecA = lightPos - P;
	vec3 vecB = occluderPos - P;
	
	float dstA = sqrt(dot(vecA, vecA));
	float dstB = sqrt(dot(vecB, vecB));
	
	if (dstA - radA / 2.0 < dstB - radB) return 1.0;
	
	float sinA = radA / dstA;
	float sinB = radB / dstB;
	
	float cosA = sqrt(1.0 - sinA * sinA);
	float cosB = sqrt(1.0 - sinB * sinB);
	
	if (cosA * dstA < cosB * dstB) return 1.0;
	
	vec3 dirA = vecA / dstA;
	vec3 dirB = vecB / dstB;
	
	float cosG = dot(dirA, dirB);
	
	if (cosG < cosA * cosB - sinA * sinB) return 1.0;
	
	float sinG = length(cross(dirA, dirB));
	
	float cscA = dstA / radA;
	float cscB = dstB / radB;
	
	float cosTheta = clamp((cosB - cosA * cosG) * cscA / sinG, -1.0, 1.0);
	float cosPhi = clamp((cosA - cosB * cosG) * cscB / sinG, -1.0, 1.0);
	
	float sinTheta = sqrt(1.0 - cosTheta * cosTheta);
	float sinPhi = sqrt(1.0 - cosPhi * cosPhi);
	
	float theta = acos(cosTheta);
	float phi = acos(cosPhi);
	
	float unoccluded = theta - cosTheta * sinTheta 
					 + (phi - cosPhi * sinPhi)
					 * cosG * sinB * sinB / (sinA * sinA);
	
	return 1.0 - unoccluded / PI;
}

float diffuse(vec3 P, vec3 N, vec3 lightPos, float lightRad)
{	// based on Seb Lagarde's Siggraph 2014 stuff - https://seblagarde.wordpress.com/
	vec3 vec = lightPos - P;
	float dst = sqrt(dot(vec, vec));
	vec3 dir = vec / dst;
	
	float cosA = dot(N, dir);
	float sinB = lightRad / dst;
	
	if (abs(cosA / sinB) > 1.0) return cosA;
	
	float sinA = length(cross(N, dir));
	float cotA = cosA / sinA;
	
	float cosB = sqrt(1.0 - sinB * sinB);
	float cotB = cosB / sinB;
	
	float x = sqrt(1.0 - cotA * cotA * cotB * cotB) * sinA;
	
	return (acos(-cotA * cotB) * cosA - x * cotB + atan(x / cotB) / (sinB * sinB)) / PI;
}

float aoFromSphere(vec3 s, float r, vec3 p, vec3 n)
{	// iq's sphere ao - http://www.iquilezles.org/www/articles/sphereao/sphereao.htm
	vec3 dir = s - p;
	float lenSq = dot(dir, dir);
	dir *= inversesqrt(lenSq);
	return 1.0 - max(dot(n, dir) * (r*r / lenSq), 0.0);
}

vec3 shade(ray r, sphere s[nSpheres], vec3 lightPos, float lightRad)
{
	if (r.s.id == -2) return vec3(8.0);
	
	vec3 P = r.o + r.d * r.t;
	vec3 N = vec3(0.0,1.0,0.0);
	vec3 C = vec3(1.0,1.0,1.0);
	
	float sh = 1.0;
	float ao = 1.0;

	if (r.s.id == -1)
	{
        float x = min(abs(fract(0.2*P.x) - 0.5), abs(fract(0.2*P.z) - 0.5));
        C *= mix(1.0, (0.4+0.5*smoothstep(0.0, 0.5/r.t, x-0.04)), exp(-0.005*r.t));
		for (int i = 0; i < nSpheres; i++)
		{
			sh *= shadow(P, lightPos, lightRad, s[i].o, s[i].r);
			ao *= aoFromSphere(s[i].o, s[i].r, P, N);
		}
	}
	else
	{
		N = normalize(P - r.s.o);
		ao *= N.y * 0.5 + 0.5;

		for (int i = 0; i < nSpheres; i++)
		{
			if (i != r.s.id)
			{
				sh *= shadow(P, lightPos, lightRad, s[i].o, s[i].r);
				ao *= aoFromSphere(s[i].o, s[i].r, P, N);
			}
		}

		sh *= max(0.0, diffuse(P, N, lightPos, lightRad));
		
		vec2 uv = hash(123.456 * float(r.s.id) / float(nSpheres));
		C = texture(iChannel0, 1.0 - uv).xyz * 0.5 + 0.25;
	}

	C *= sh * 0.95 + 0.05;
	C *= mix(ao, 1.0, sh);
	
	return C;
}

void intersect(inout ray r, sphere s)
{
	vec3 o = r.o - s.o;
	
	float b = dot(r.d, o);
	float c = dot(o, o) - s.r*s.r;
	float d = b*b - c;
	
	if (d < 0.0) return;
	
	float t = -b - sqrt(d);
	if (t <= 0.0 || t > r.t ) return;
	
	r.t = t;
	r.s = s;
}

vec3 trace(vec3 ro, vec3 rd)
{
	ray r = ray(ro, rd, 4000.0, sphere(vec3(0.0,0.0,0.0), 4000.0, -1));
	if (r.d.y < 0.0) r.t = min(r.t, -ro.y / rd.y);
	
	float time = iTime + 90.0;
	float d = 0.0;
	sphere s[nSpheres];
	
	for (int i = 0; i < nSpheres; i++) 
	{
		float f = float(i) / float(nSpheres);
		vec2 h = hash(f + 0.5);
		float rad = h.x * 1.5 + 0.5;
		vec3 pos =  vec3(cos(time * f * 0.1), 0.0, sin(time * f * 0.1)) * (d + rad);
		pos.y += rad + (sin(time * (1.0 - f) * 0.2) * 0.1 + 0.1) * d;
		s[i] = sphere(pos, rad, i);
		intersect(r, s[i]);
		d += rad * 2.0;
	}
	
	float lightRad = cos(iTime*0.25) * 20.0 + 21.0;
	vec3 lightPos = vec3(sin(iTime*0.1) * 24.0 + 12.0, d * 0.25 + lightRad + 5.0, -4.0);
	intersect(r, sphere(lightPos, lightRad, -2));
	
	return shade(r, s, lightPos, lightRad);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = 2.0 * fragCoord.xy - iResolution.xy;
	uv /= max(iResolution.x, iResolution.y);
	
	vec2 m = vec2(2.3,1.3);
	if (iMouse.xy != vec2(0.0))
		m = vec2(2.0 * PI, 0.4) * iMouse.xy / iResolution.xy + vec2(-PI / 2.0, 1.0);
	
	vec3 ro = vec3(sin(m.y)*cos(m.x), cos(m.y), sin(m.y)*sin(m.x)) * camDist;
	mat3 r = mat3(normalize(cross(-ro,vec3(0.0,1.0,0.0))), 0.0,1.0,0.0, -normalize(ro));
	r[1] = cross(r[0],r[2]);
	vec3 rd = normalize(r * vec3(uv,1.0));
	
	vec3 C = trace(ro, rd);
	
	#ifdef POST_TONEMAP
	// tonemapping by Jim Hejl and Richard Burgess-Dawson
	float exposure = 1.4;
	C = max(vec3(0.0,0.0,0.0), C * exposure - 0.004);
	C = (C * (6.2 * C + 0.5)) / (C * (6.2 * C + 1.7) + 0.06);
	#endif
	
	#ifdef POST_DITHER
	C += hash(uv) * 0.02 - 0.01;
	#endif
	
	#ifdef POST_VIGNETTE
	C *= sqrt(1.0 - dot(uv, uv) * 0.25);
	#endif
	
	fragColor= vec4(C, 1.0 );
}'''


class StSoftSphereShadows(GlslSource):
    name = 'st_soft_sphere_shadows'
    label = 'Soft Sphere Shadows'
    description = "Trying to get correct shadows from spherical lights/occluders. I'm sure someone with an actual education could do it much more elegantly, but I'm pleased with..."
    dialect = 'st'
    GLSL = GLSL
    channels = {0: 'noise'}
    params = scene_params({}, render_scale=0.5)
