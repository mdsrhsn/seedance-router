import os, json, time, requests, sys
RAW = os.environ.get("XI_KEY", "")
K = RAW.strip().strip('"').strip("'").strip()
print("key len raw", len(RAW), "clean", len(K), "starts sk_", K.startswith("sk_"))
H = {"xi-api-key": K, "Content-Type": "application/json"}
B = "https://api.elevenlabs.io/v1/flows/video"
PROMPT = ("Cinematic 10-second shot: a footballer in a navy and gold kit juggles a ball on a "
          "sunlit stadium pitch at golden hour, slow push-in camera, shallow depth of field, realistic.")
log = []
def L(*a):
    s = " ".join(str(x) for x in a); print(s); log.append(s)
tries = [
    ("bytedance-seedance-v2.5", {"duration": 10, "resolution": "720p", "aspect_ratio": "16:9", "generate_audio": True}),
    ("bytedance-seedance-v2", {"duration": 10, "resolution": "720p", "aspect_ratio": "16:9"}),
    ("veo-3.1-fast-generate-001", {"duration": 8, "aspect_ratio": "16:9"}),
]
L("keyinfo rawlen", len(RAW), "cleanlen", len(K), "sk_", K.startswith("sk_"))
for f,v in [("duration_seconds",10),("seconds",10),("length",10),("duration_s",10),("video_duration",10),("resolution","720p"),("aspect_ratio","16:9"),("generate_audio",True),("audio_enabled",True),("enable_audio",True),("text",PROMPT),("negative_prompt","x")]:
    r = requests.post(B, headers=H, json={"model_id":"bytedance-seedance-v2.5","prompt":PROMPT,f:v}, timeout=60)
    L("probe", f, r.status_code, r.text[:300])
r = requests.get("https://api.elevenlabs.io/v1/models", headers=H, timeout=60); L("models", r.status_code, r.text[:200])
gid = None
for model, extra in tries:
    body = {"model_id": model, "prompt": PROMPT, **extra}
    r = requests.post(B, headers=H, json=body, timeout=60)
    L("POST", model, r.status_code, r.text[:3000])
    if r.status_code == 422:
        # retry with minimal body to learn schema
        r = requests.post(B, headers=H, json={"model_id": model, "prompt": PROMPT}, timeout=60)
        L("POST-min", model, r.status_code, r.text[:3000])
    if r.ok:
        gid = r.json().get("id"); used = model; break
if gid:
    for i in range(120):
        time.sleep(10)
        g = requests.get(f"{B}/{gid}", headers=H, timeout=60)
        j = g.json() if g.ok else {}
        L("poll", i, g.status_code, json.dumps(j)[:1500])
        st = j.get("status")
        if st in ("completed", "failed"): break
    if j.get("status") == "completed":
        def urls(o):
            if isinstance(o, dict):
                for v in o.values(): yield from urls(v)
            elif isinstance(o, list):
                for v in o: yield from urls(v)
            elif isinstance(o, str) and o.startswith("http"): yield o
        for u in urls(j):
            d = requests.get(u, timeout=300)
            if d.ok and len(d.content) > 10000:
                open("out/video.mp4", "wb").write(d.content); L("saved", len(d.content), "bytes from", used); break
open("out/log.txt", "w").write("\n".join(log))
