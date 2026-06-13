// Runtime: adjust the playback speed of a ROSE animated object (banner, water).
// Lives on each spawned animated object next to its Animator. Tweak `speed` in
// the Inspector at edit- or play-time, or set it from your game code:
//     obj.GetComponent<RoseAnimationSpeed>().speed = 2f;
using UnityEngine;

[RequireComponent(typeof(Animator))]
public class RoseAnimationSpeed : MonoBehaviour
{
    [Tooltip("1 = normal, 2 = double speed, 0 = paused")]
    [Range(0f, 8f)] public float speed = 1f;

    Animator _anim;

    void Awake() { _anim = GetComponent<Animator>(); Apply(); }
    void OnValidate() { if (_anim == null) _anim = GetComponent<Animator>(); Apply(); }
    void Update() { if (_anim && !Mathf.Approximately(_anim.speed, speed)) Apply(); }

    void Apply() { if (_anim) _anim.speed = speed; }
}
