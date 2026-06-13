// ROSE-style cloud skybox for Unity URP.
// Soft blue gradient (horizon -> zenith) with drifting procedural puffy clouds,
// like ROSE Online's daytime sky. Assign the material to:
//   Window > Rendering > Lighting > Environment > Skybox Material
// (the ROSE editor script's "Apply Sky" menu does this for you).
Shader "ROSE/Skybox"
{
    Properties
    {
        _HorizonColor ("Horizon Color", Color) = (0.80, 0.89, 1.00, 1)
        _ZenithColor  ("Zenith Color",  Color) = (0.33, 0.55, 0.88, 1)
        _CloudColor   ("Cloud Color",   Color) = (1, 1, 1, 1)
        _Exponent     ("Gradient Falloff", Range(0.2, 3)) = 0.7
        _CloudScale   ("Cloud Scale", Range(0.2, 6)) = 1.6
        _CloudSpeed   ("Cloud Speed", Range(0, 0.5)) = 0.012
        _CloudCover   ("Cloud Cover", Range(0, 1)) = 0.52
        _CloudSoftness("Cloud Softness", Range(0.01, 0.6)) = 0.28
        _CloudDensity ("Cloud Density", Range(0, 1)) = 0.9
    }

    SubShader
    {
        Tags { "RenderPipeline"="UniversalPipeline" "Queue"="Background" "RenderType"="Background" "PreviewType"="Skybox" }
        Cull Off ZWrite Off

        Pass
        {
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            CBUFFER_START(UnityPerMaterial)
                half4 _HorizonColor, _ZenithColor, _CloudColor;
                half  _Exponent, _CloudScale, _CloudSpeed, _CloudCover, _CloudSoftness, _CloudDensity;
            CBUFFER_END

            struct Attributes { float4 positionOS : POSITION; };
            struct Varyings   { float4 positionCS : SV_POSITION; float3 dir : TEXCOORD0; };

            Varyings vert(Attributes IN)
            {
                Varyings OUT;
                OUT.positionCS = TransformObjectToHClip(IN.positionOS.xyz);
                OUT.dir = IN.positionOS.xyz;     // direction from sky-dome centre
                return OUT;
            }

            float hash(float2 p) { return frac(sin(dot(p, float2(127.1, 311.7))) * 43758.5453); }

            float vnoise(float2 p)
            {
                float2 i = floor(p), f = frac(p);
                f = f * f * (3.0 - 2.0 * f);
                float a = hash(i), b = hash(i + float2(1, 0));
                float c = hash(i + float2(0, 1)), d = hash(i + float2(1, 1));
                return lerp(lerp(a, b, f.x), lerp(c, d, f.x), f.y);
            }

            float fbm(float2 p)
            {
                float v = 0.0, amp = 0.5;
                [unroll] for (int k = 0; k < 5; k++) { v += amp * vnoise(p); p *= 2.02; amp *= 0.5; }
                return v;
            }

            half4 frag(Varyings IN) : SV_Target
            {
                float3 dir = normalize(IN.dir);
                float up = saturate(dir.y);

                // gradient sky
                half3 sky = lerp(_HorizonColor.rgb, _ZenithColor.rgb, pow(up, _Exponent));

                // clouds: project the dome onto a plane and drift over time
                float2 uv = (dir.xz / max(dir.y, 0.06)) * _CloudScale;
                uv += _Time.y * _CloudSpeed * float2(1.0, 0.35);
                float n = fbm(uv);
                float clouds = smoothstep(_CloudCover, _CloudCover + _CloudSoftness, n);
                clouds *= saturate(dir.y * 5.0);          // fade out near the horizon
                clouds *= _CloudDensity;

                half3 col = lerp(sky, _CloudColor.rgb, clouds);
                return half4(col, 1.0);
            }
            ENDHLSL
        }
    }
    Fallback Off
}
