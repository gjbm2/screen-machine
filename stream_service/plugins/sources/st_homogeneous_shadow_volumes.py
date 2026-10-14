"""Homogeneous Shadow Volumes: Click and drag to move the light. The projection plane is shown in red, along with the projection of each vertex in blue. The grey line passing through the...

Source: https://www.shadertoy.com/view/ttsBR7
Author: skye_adaire
License: Apache-2.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: Apache License 2.0
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
Created by Skye Adaire
Apache License 2.0

This algorithm produces shadow volumes by treating the projection
of a set of triangles as a homogenized implicit surface.

An implicit function takes a "position" and outputs a scalar.
	f(x, y, ...) = level
	when level < 0 the position is inside the surface
	when level = 0 the position is on the surface
	when level > 0 the position is outside the surface

A function can be homogenized by:
	f(x, y) -> w^(degree(f)) * f(x / w, y / w) = h(x, y, w)

This easily generalizes to 3D.
In 2D our "face" is a line segment, and our simplex is a triangle.
In 3D our face is a triangle, and our simplex is a tetrahedron.

The algorithm:
1. Project an object's triangles onto a light's projection plane
	(shown in red). I refer to the projected object as the "umbra mesh."
2. For each face of the object, determine whether that face is
	towards the light.
3. For faces towards the light (even if occluded) form a "light simplex"
	(faces included are shown in green)
4. For faces away from the light form a "shadow volume"
	(faces included are shown in pink)
5. For a point in the world, translate and rotate relative to the light
	(or use a generalized projective plane)
6. Evaluate the point against each light simplex and shadow volume.
7. If a point is included in more shadow volumes than light simplex,
	it is shadowed.

Notes:
For points not shadowed, the minimum level curve across all shadow
volumes could be used as a distance estimate to the nearest shadow.
This could be used to make a penumbra region for soft shadows.

For homogenization, multiplying by w is not required, but doing so
makes the shadow level curves appear more regularly spaced.

For a scene with multiple objects and lights, an umbra mesh
could be computed and stored for each pair of object and light.
*/

#define SHOW_LEVEL_CURVES false

vec2 rotateCW(vec2 n)
{
    return vec2(n.y, -n.x);
}

mat3 makeLightTransform(vec2 origin, vec2 normal)
{
    return mat3(rotateCW(normal), 0, normal, 0, origin, 1);
}

mat3 makeProjection(vec3 v, vec3 p)
{
 	return mat3(v * p[0], v * p[1], v * p[2]) - mat3(dot(v, p));
}

vec2 hmul1(in mat3 m, in vec2 v)
{
 	vec3 hv = m * vec3(v, 1);
    return hv.xy / hv.z;
}

//this is to make the generalization more apparent
#define vec1 float

vec1 projectWorldPositionToLight(in mat3 projectToPlane, in mat3 worldToLight, vec2 p)
{
    vec2 pRelLight = hmul1(worldToLight, p);
    vec2 pProjected = hmul1(projectToPlane, pRelLight);
    return pProjected.x;
}

float A_intersection(float a, float b)
{
 	return max(a, b);
}

float A_union(float a, float b)
{
 	return min(a, b);
}

//implicit half plane given two points on it's surface
float A_halfPlane(vec2 a, vec2 b, vec2 p)
{
    b = a + normalize(b - a);//make it euclidean
    return (b.y - a.y) * p.x - (b.x - a.x) * p.y + b.x * a.y - b.y * a.x;
}

//implicit triangle with non-euclidean level curves, winding order is important
float A_triangle(vec2 v0, vec2 v1, vec2 v2, vec2 p)
{
    return
        A_intersection(A_halfPlane(v0, v1, p),
   		A_intersection(A_halfPlane(v1, v2, p),
   		A_halfPlane(v2, v0, p)));
}

//implicit 1D disk / interval / segment
float A_disk(vec1 a, vec1 b, vec1 p)
{
    //return (p - a) * (p - b);//algebraic, but not euclidean
	return max(a - p, p - b);//euclidean
}

float umbraHomogeneous(vec1 v0, vec1 v1, vec2 p)
{
 	return /*p.y * */ A_disk(v0, v1, p.x / p.y);
}

bool isCounterClockwise(vec2 a, vec2 b, vec2 c)
{
    return cross(vec3(b - a, 0), vec3(c - a, 0)).z < 0.0;
}

void orderTriangleCW(inout vec2 v0, inout vec2 v1, inout vec2 v2)
{
    if(isCounterClockwise(v0, v1, v2))
    {
        vec2 t = v1;
        v1 = v2;
        v2 = t;
    }
}

void orderVolumes(inout vec1 v0, inout vec1 v1)
{
 	if(v1 < v0)
    {
     	float t = v0;
        v0 = v1;
        v1 = t;
    }
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 color = vec3(0);

    float modelScale = 11.0;
    vec2 mouseUV = iMouse.xy / iResolution.xy;
    vec2 mouseUnit = mouseUV - 0.5;
    vec2 mouseRatio = mouseUnit * vec2(iResolution.x / float(iResolution.y), 1.0);
    vec2 mouseWorld = modelScale * mouseRatio;

    //the light is the point at infinity
    float timeScale = iTime * 0.2;
    vec2 defaultLightPosition = vec2(cos(timeScale), sin(timeScale)) * 5.0;
    vec2 lightPosition = iMouse.z <= 0.0 ? defaultLightPosition : mouseWorld;
    vec2 lightNormal = -normalize(lightPosition);
    mat3 lightToWorld = makeLightTransform(lightPosition, lightNormal);
    mat3 worldToLight = inverse(lightToWorld);

    //these are fixed relative to the light, which is why we transform rel light
    vec3 projectionPosition = vec3(0, 0, 1);
    vec3 projectionPlane = vec3(0, 1, -1.0);
    mat3 projectToPlane = makeProjection(projectionPosition, projectionPlane);

    vec2 uvPosition = fragCoord/iResolution.xy;
    vec2 unitPosition = uvPosition - 0.5;
    vec2 ratioPosition = unitPosition * vec2(iResolution.x / float(iResolution.y), 1.0);
    vec2 modelPosition = modelScale * ratioPosition;
    vec2 positionRelLight = hmul1(worldToLight, modelPosition);

    #define vertexCount 9
    vec2 objectPositions[vertexCount];
    objectPositions[0] = vec2(-1.0, -1.3);
    objectPositions[1] = vec2(3.5, 1.3);
    objectPositions[2] = vec2(0.2, 0.2);
    objectPositions[3] = vec2(-0.1, 3.0);
    objectPositions[4] = vec2(-1.5, 1.0);
    objectPositions[5] = vec2(-4.0, 0.5);
    objectPositions[6] = vec2(3.0, -1.0);
    objectPositions[7] = vec2(2.5, -1.5);
    objectPositions[8] = vec2(3.5, -1.5);

    #define triangleCount 4
    ivec3 triangles[triangleCount];
    triangles[0] = ivec3(0, 1, 2);
    triangles[1] = ivec3(2, 3, 0);
    triangles[2] = ivec3(0, 4, 5);
    triangles[3] = ivec3(6, 7, 8);

    vec1 umbraMesh[vertexCount];//the projected object

    for(int i = 0; i < vertexCount; i++)
    {
     	umbraMesh[i] = projectWorldPositionToLight(projectToPlane, worldToLight, objectPositions[i]);
    }

    //identify the faces pointing towards and away from the light
    int lightSimplexCount = 0;
    ivec2 lightSimplexes[10];
    int shadowVolumeCount = 0;
    ivec2 shadowVolumes[10];

    for(int i = 0; i < triangleCount; i++)
    {
        int v0 = triangles[i].x;
        int v1 = triangles[i].y;
        int v2 = triangles[i].z;

        vec2 i0 = lightPosition - objectPositions[v0];
        vec2 i1 = lightPosition - objectPositions[v1];
        vec2 i2 = lightPosition - objectPositions[v2];

        vec2 n0 = rotateCW(objectPositions[v1] - objectPositions[v0]);
        vec2 n1 = rotateCW(objectPositions[v2] - objectPositions[v1]);
        vec2 n2 = rotateCW(objectPositions[v0] - objectPositions[v2]);

       	if(dot(n0, i0) >= 0.0)
        {
            lightSimplexes[lightSimplexCount] = ivec2(v0, v1);
            lightSimplexCount++;
        }
        else
        {
            shadowVolumes[shadowVolumeCount] = ivec2(v0, v1);
            shadowVolumeCount++;
        }

        if(dot(n1, i1) >= 0.0)
        {
            lightSimplexes[lightSimplexCount] = ivec2(v1, v2);
            lightSimplexCount++;
        }
        else
        {
            shadowVolumes[shadowVolumeCount] = ivec2(v1, v2);
            shadowVolumeCount++;
        }

        if(dot(n2, i2) >= 0.0)
        {
            lightSimplexes[lightSimplexCount] = ivec2(v2, v0);
            lightSimplexCount++;
        }
        else
        {
            shadowVolumes[shadowVolumeCount] = ivec2(v2, v0);
            shadowVolumeCount++;
        }
    }

    int simplexShadowParity = 0;

    //form a simplex with each face and the point at infinity
    for(int i = 0; i < lightSimplexCount; i++)
    {
		vec2 p0 = objectPositions[lightSimplexes[i].x];
        vec2 p1 = objectPositions[lightSimplexes[i].y];
        vec2 p2 = lightPosition;
       	orderTriangleCW(p0, p1, p2);
        
        float level = A_triangle(p0, p1, p2, modelPosition);

        if(level < 0.0)
        {
            simplexShadowParity += 1;
        }
    }

    float shadowLevel = 1e10;

    if(positionRelLight.y > 0.0)//exclude the volume behind the light
    {
        for(int i = 0; i < shadowVolumeCount; i++)
        {
			vec1 v0 = umbraMesh[shadowVolumes[i].y];
            vec1 v1 = umbraMesh[shadowVolumes[i].x];
            orderVolumes(v0, v1);

            float level = umbraHomogeneous(v0, v1, positionRelLight);

            shadowLevel = A_union(level, shadowLevel);

            if(level < 0.0)
            {
                simplexShadowParity -= 1;
            }
        }
    }

    if(simplexShadowParity < 0)//the position is in shadow
    {
     	color += vec3(0.2);

        if(SHOW_LEVEL_CURVES)
        {
            color -= pow(cos(5.0 * shadowLevel * 3.14159), 40.0) * 0.1;
        }
    }
    else //the position is in light
    {
     	color += vec3(0.6);

        if(SHOW_LEVEL_CURVES)
        {
            color += pow(cos(5.0 * shadowLevel * 3.14159), 40.0) * 0.1;
        }
    }

    //shade the border of the positive half plane through the light
    if(abs(positionRelLight.y) < 0.05)
    {
     	color = vec3(0.5);
    }

    vec3 lightEdgeColor = vec3(0.5, 0.9, 0.5);
    vec3 shadowEdgeColor = vec3(0.86, 0.39, 0.73);
    float edgeWidth = 0.1;

    //draw triangles
    for(int i = 0; i < triangleCount; i++)
    {
        int v0 = triangles[i].x;
        int v1 = triangles[i].y;
        int v2 = triangles[i].z;

        if(A_triangle(objectPositions[v0], objectPositions[v1], objectPositions[v2], modelPosition) < 0.0)
        {
            color = vec3(1);

            //shade edges based on parity

            for(int j = 0; j < lightSimplexCount; j++)
            {
             	ivec2 s = lightSimplexes[j];

                if(abs(A_halfPlane(objectPositions[v0], objectPositions[v1], modelPosition)) < edgeWidth)
                {
                    if(s == ivec2(v0, v1) || s == ivec2(v1, v0))
                    {
                        color = lightEdgeColor;
                    }
                }
                else if(abs(A_halfPlane(objectPositions[v1], objectPositions[v2], modelPosition)) < edgeWidth)
                {
                    if(s == ivec2(v1, v2) || s == ivec2(v2, v1))
                    {
                        color = lightEdgeColor;
                    }
                }
                else if(abs(A_halfPlane(objectPositions[v2], objectPositions[v0], modelPosition)) < edgeWidth)
                {
                    if(s == ivec2(v2, v0) || s == ivec2(v0, v2))
                    {
                        color = lightEdgeColor;
                    }
                }
            }

            for(int j = 0; j < shadowVolumeCount; j++)
            {
             	ivec2 s = shadowVolumes[j];

                if(abs(A_halfPlane(objectPositions[v0], objectPositions[v1], modelPosition)) < edgeWidth)
                {
                    if(s == ivec2(v0, v1) || s == ivec2(v1, v0))
                    {
                        color = shadowEdgeColor;
                    }
                }
                else if(abs(A_halfPlane(objectPositions[v1], objectPositions[v2], modelPosition)) < edgeWidth)
                {
                    if(s == ivec2(v1, v2) || s == ivec2(v2, v1))
                    {
                        color = shadowEdgeColor;
                    }
                }
                else if(abs(A_halfPlane(objectPositions[v2], objectPositions[v0], modelPosition)) < edgeWidth)
                {
                    if(s == ivec2(v2, v0) || s == ivec2(v0, v2))
                    {
                        color = shadowEdgeColor;
                    }
                }
            }
        }
    }

    //draw real line
    if(abs(positionRelLight.y - 1.0) < 0.05)
    {
     	color = vec3(1.0, 0.2, 0.1);
    }

    vec3 vertexColor = vec3(0.2, 1, 1);

    //draw vertices
    for(int i = 0; i < vertexCount; i++)
    {
        //R2
        if(length(modelPosition - objectPositions[i]) < 0.1)
        {
            color = vertexColor;
        }

        //projection in R1
        if(abs(positionRelLight.y - 1.0) < 0.05 && abs(positionRelLight.x - umbraMesh[i]) < 0.1)
        {
            color = vertexColor;
        }
    }

    //draw dot for light
    if(length(positionRelLight) < 0.2)
    {
        color = vec3(1);
    }

    fragColor = vec4(color,1.0);
}'''


class StHomogeneousShadowVolumes(GlslSource):
    name = 'st_homogeneous_shadow_volumes'
    label = 'Homogeneous Shadow Volumes'
    description = 'Click and drag to move the light. The projection plane is shown in red, along with the projection of each vertex in blue. The grey line passing through the...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
