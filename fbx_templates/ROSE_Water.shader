// ROSE/Water — animated translucent water for the exported OCEAN surfaces (URP).
// Procedural: two crossing sine ripples drive the normal for moving specular,
// a Fresnel term fades deep->shallow colour and adds rim brightness, and an
// optional scrolling texture tints the surface. No depth texture needed, so it
// works in any URP setup. Assigned by AssignRoseMaterials to kind="water".
Shader "ROSE/Water"
{
    Properties
    {
        _DeepColor    ("Deep Color",    Color) = (0.05, 0.22, 0.36, 0.85)
        _ShallowColor ("Shallow Color", Color) = (0.18, 0.52, 0.66, 0.55)
        _BaseMap      ("Water Texture (optional)", 2D) = "white" {}
        _Tiling       ("World Tiling",   Float) = 0.0006
        _ScrollSpeed  ("Scroll (xy/zw)", Vector) = (0.012, 0.008, -0.009, 0.011)
        _WaveScale    ("Ripple Scale",   Float) = 0.012
        _WaveSpeed    ("Ripple Speed",   Float) = 1.4
        _WaveStrength ("Ripple Strength",Range(0,2)) = 0.5
        _Smoothness   ("Smoothness",     Range(0,1)) = 0.9
        _SpecIntensity("Specular",       Range(0,4)) = 1.5
        _FresnelPower ("Fresnel Power",  Range(0.5,8)) = 4
    }

    SubShader
    {
        Tags { "RenderType"="Transparent" "Queue"="Transparent" "RenderPipeline"="UniversalPipeline" }

        Pass
        {
            Name "ForwardLit"
            Tags { "LightMode"="UniversalForward" }
            Blend SrcAlpha OneMinusSrcAlpha
            ZWrite Off
            Cull Off

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile _ _MAIN_LIGHT_SHADOWS _MAIN_LIGHT_SHADOWS_CASCADE
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"

            TEXTURE2D(_BaseMap); SAMPLER(sampler_BaseMap);

            CBUFFER_START(UnityPerMaterial)
                float4 _DeepColor;
                float4 _ShallowColor;
                float4 _ScrollSpeed;
                float  _Tiling;
                float  _WaveScale;
                float  _WaveSpeed;
                float  _WaveStrength;
                float  _Smoothness;
                float  _SpecIntensity;
                float  _FresnelPower;
            CBUFFER_END

            struct Attributes { float4 positionOS : POSITION; float2 uv : TEXCOORD0; };
            struct Varyings   { float4 positionHCS : SV_POSITION; float3 positionWS : TEXCOORD0; };

            Varyings vert (Attributes IN)
            {
                Varyings OUT;
                OUT.positionWS  = TransformObjectToWorld(IN.positionOS.xyz);
                OUT.positionHCS = TransformWorldToHClip(OUT.positionWS);
                return OUT;
            }

            half4 frag (Varyings IN) : SV_Target
            {
                float t = _Time.y * _WaveSpeed;
                float2 p = IN.positionWS.xz;                  // Y-up world: ripple in the XZ plane

                // two crossing sine ripples -> analytic gradient -> perturbed normal
                float2 k1 = float2(0.9, 0.5) * _WaveScale;
                float2 k2 = float2(-0.6, 1.1) * _WaveScale;
                float2 grad = k1 * cos(dot(p, k1) + t) + k2 * cos(dot(p, k2) + t * 1.27);
                grad *= _WaveStrength;
                float3 N = normalize(float3(-grad.x, 1.0, -grad.y));

                float3 V = normalize(_WorldSpaceCameraPos - IN.positionWS);
                float  fres = pow(saturate(1.0 - saturate(dot(N, V))), _FresnelPower);

                // base colour: deep -> shallow by fresnel, tinted by a scrolling texture
                half4 col = lerp(_DeepColor, _ShallowColor, fres);
                float2 uv = p * _Tiling;
                half3 tex = (SAMPLE_TEXTURE2D(_BaseMap, sampler_BaseMap, uv + _ScrollSpeed.xy * _Time.y).rgb
                           + SAMPLE_TEXTURE2D(_BaseMap, sampler_BaseMap, uv * 1.7 + _ScrollSpeed.zw * _Time.y).rgb) * 0.5;
                col.rgb *= tex;

                // main-light specular off the rippled normal
                Light mainLight = GetMainLight();
                float3 H = normalize(mainLight.direction + V);
                float  spec = pow(saturate(dot(N, H)), lerp(16.0, 400.0, _Smoothness)) * _SpecIntensity;

                col.rgb += mainLight.color * spec + fres * 0.25;
                col.a    = saturate(col.a + fres * 0.3);
                return col;
            }
            ENDHLSL
        }
    }
    Fallback "Universal Render Pipeline/Unlit"
}
