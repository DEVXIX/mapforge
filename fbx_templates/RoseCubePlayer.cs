// Drive-a-cube player (new Input System), kinematic + raycast ground-stick.
//   W A S D    : move around the map   Left Shift : sprint
// A camera trails behind/above. Kinematic (not physics) because the ROSE map
// sits at very large world coordinates where a CharacterController jitters.
// Add via menu: ROSE > Add Cube Player (Spawn on Map).
using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

[AddComponentMenu("ROSE/Rose Cube Player")]
public class RoseCubePlayer : MonoBehaviour
{
    public float moveSpeed = 3000f;          // world units / second (map is in ROSE cm)
    public float sprintMultiplier = 4f;
    public float turnSpeed = 12f;

    public bool stickToGround = true;
    public float groundOffset = 150f;        // how far the cube centre sits above the ground
    public float rayUp = 5000f;              // start the ground ray this far above the cube
    public float rayDown = 40000f;

    public Camera followCam;
    public Vector3 camOffset = new Vector3(0f, 1200f, -2400f);
    public float camLerp = 0.12f;

    void Awake() { if (followCam == null) followCam = Camera.main; }

    void Update()
    {
#if ENABLE_INPUT_SYSTEM
        var kb = Keyboard.current;
        Vector2 input = Vector2.zero;
        bool sprint = false;
        if (kb != null)
        {
            if (kb.wKey.isPressed) input.y += 1f;
            if (kb.sKey.isPressed) input.y -= 1f;
            if (kb.dKey.isPressed) input.x += 1f;
            if (kb.aKey.isPressed) input.x -= 1f;
            sprint = kb.leftShiftKey.isPressed || kb.rightShiftKey.isPressed;

            if (kb.zKey.wasPressedThisFrame) TeleportToFountain();   // Z = jump to the fountain
        }

        Vector3 wish = new Vector3(input.x, 0f, input.y);     // world XZ
        if (wish.sqrMagnitude > 1f) wish.Normalize();
        float spd = moveSpeed * (sprint ? sprintMultiplier : 1f);

        Vector3 pos = transform.position + wish * spd * Time.deltaTime;
        if (stickToGround &&
            Physics.Raycast(pos + Vector3.up * rayUp, Vector3.down, out RaycastHit hit, rayUp + rayDown))
            pos.y = hit.point.y + groundOffset;
        transform.position = pos;

        if (wish.sqrMagnitude > 0.01f)
            transform.rotation = Quaternion.Slerp(transform.rotation,
                Quaternion.LookRotation(wish, Vector3.up), turnSpeed * Time.deltaTime);
#endif
    }

    // Z: teleport to the fountain (its pool sits exactly at it). Also snaps the
    // follow camera so you don't fly across the map.
    void TeleportToFountain()
    {
        var f = GameObject.Find("FountainPool");
        if (f == null) return;
        Vector3 p = f.transform.position;
        p.y += Mathf.Max(groundOffset, 1f);
        transform.position = p;
        if (followCam != null) followCam.transform.position = p + camOffset;
    }

    void LateUpdate()
    {
        if (followCam == null) return;
        Vector3 desired = transform.position + camOffset;       // fixed world offset (no spin)
        followCam.transform.position = Vector3.Lerp(followCam.transform.position, desired, camLerp);
        followCam.transform.LookAt(transform.position);
    }
}
