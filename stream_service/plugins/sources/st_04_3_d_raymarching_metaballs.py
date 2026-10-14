"""04 3 D Raymarching metaballs: Example of simple raymarching

Source: https://www.shadertoy.com/view/WtVSzh
Author: waffles
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Licensed under Creative Commons CC0: https://creativecommons.org/share-your-work/public
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Licensed under Creative Commons CC0: https://creativecommons.org/share-your-work/public-domain/cc0/

// For more shapes and raymarching knowledge: https://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    float time = iTime;
    vec2 uv = fragCoord/iResolution.xy*2.0f-1.0f;

    // initial background color
    vec4 color = vec4(0.0f,0.0f,0.0f,0.0f);
    // palette
    vec3 color1 = vec3(1.0f, 1.0f, 1.0f);
    vec3 color2 = vec3(0.4f, 0.4f, 0.4f);

    float metaStickyness = 0.4f;
    float sphereSize = 0.3f;

    vec3 spheres[5];
    spheres[0].x = cos(time)*0.5f+0.2f;
    spheres[0].y = sin(time)*0.8f+0.3f;
    spheres[0].z = sin(time)*0.5f;
    spheres[1].x = sin(time)*0.5f-0.5f;
    spheres[1].y = cos(time)*0.5f-0.25f;
    spheres[1].z = sin(time)+0.5f;
    spheres[2].x = cos(time)*0.5f-0.5f;
    spheres[2].y = sin(time)*0.7f-0.25f;
    spheres[2].z = cos(time)*0.6f+0.3f;
    spheres[3].x = cos(time)*0.8f;
    spheres[3].y = sin(time)*0.4f;
    spheres[3].z = cos(time)*0.8f;
    spheres[4].x = sin(time)*0.9f;
    spheres[4].y = sin(time)*0.9f;
    spheres[4].z = sin(time)*0.2f;

    vec3 cameraEye = vec3(0.0f,0.0f,-2.5f);
    vec3 cameraUp = vec3(0.0f,1.0f,0.0f);
    vec3 cameraRight = vec3(1.0f,0.0f,0.0f);

    vec3 cameraTarget = normalize(cross(cameraRight, cameraUp) + cameraRight*uv.x + cameraUp*uv.y);

    float rayHitThreshold = 0.01f;
    float zFar = 5.0f;
    float rayDistance = 0.0f;
    int rayMaxSteps = 30;
    for(int i = 0; i < rayMaxSteps; i++)
    {
        vec3 rayPosition = cameraEye+cameraTarget*rayDistance;

        float distanceToSolid = 0.0f;

        for(int i = 0; i < spheres.length(); i++)
        {
            float sa = length(rayPosition+spheres[i])-sphereSize;
            if (i == 0)
            {
                distanceToSolid = sa;
            }
            else
            {
                //metaball glue
                float h = clamp(0.5f+0.5f*(sa-distanceToSolid)/metaStickyness,0.0f,1.0f);
                distanceToSolid = mix(sa,distanceToSolid,h)-metaStickyness*h*(1.0f-h);
            }
        }

        if (rayDistance > zFar)
        {
            break;
        }
        else if (distanceToSolid < rayHitThreshold)
        {
            vec3 n = rayPosition;

            float percent = abs((n.r+n.g+n.b)/3.0f);
            color = vec4(mix(color1, color2, percent/0.5f), 1.0f);

            break;
        }

        rayDistance += distanceToSolid;
    }

    fragColor = color;
}'''


class St043DRaymarchingMetaballs(GlslSource):
    name = 'st_04_3_d_raymarching_metaballs'
    label = '04 3 D Raymarching metaballs'
    description = 'Example of simple raymarching'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
