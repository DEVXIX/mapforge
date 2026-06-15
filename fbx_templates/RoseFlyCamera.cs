// Free-fly spectator camera (new Input System).
//   Hold RIGHT mouse  : look around
//   W A S D            : move (relative to where you look)
//   E / Space          : up      Q / Ctrl : down
//   Left Shift         : move faster
// Attach to a Camera; or use menu  ROSE > Add Fly Camera (Spawn on Map).
using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

[AddComponentMenu("ROSE/Rose Fly Camera")]
public class RoseFlyCamera : MonoBehaviour
{
    public float speed = 60f;            // metres / second
    public float fastMultiplier = 6f;    // while holding Shift
    public float lookSensitivity = 0.1f; // degrees per mouse-pixel

    float yaw, pitch;

    void Start()
    {
        var e = transform.eulerAngles;
        yaw = e.y; pitch = e.x;
    }

    void Update()
    {
#if ENABLE_INPUT_SYSTEM
        var kb = Keyboard.current;
        var mouse = Mouse.current;
        if (kb == null) return;

        if (mouse != null && mouse.rightButton.isPressed)
        {
            Vector2 d = mouse.delta.ReadValue();
            yaw += d.x * lookSensitivity;
            pitch -= d.y * lookSensitivity;
            pitch = Mathf.Clamp(pitch, -89f, 89f);
            transform.rotation = Quaternion.Euler(pitch, yaw, 0f);
        }

        float mult = (kb.leftShiftKey.isPressed || kb.rightShiftKey.isPressed) ? fastMultiplier : 1f;
        float s = speed * mult * Time.deltaTime;
        Vector3 m = Vector3.zero;
        if (kb.wKey.isPressed) m += transform.forward;
        if (kb.sKey.isPressed) m -= transform.forward;
        if (kb.dKey.isPressed) m += transform.right;
        if (kb.aKey.isPressed) m -= transform.right;
        if (kb.eKey.isPressed || kb.spaceKey.isPressed) m += Vector3.up;
        if (kb.qKey.isPressed || kb.leftCtrlKey.isPressed) m -= Vector3.up;
        transform.position += m * s;
#endif
    }
}
